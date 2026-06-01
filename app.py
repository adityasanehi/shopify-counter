from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import requests
import os
import logging
import hmac
import hashlib
import base64
import threading
import time
from datetime import datetime, timedelta, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

if os.getenv('FLASK_ENV') == 'production':
    allowed_origins = os.getenv('ALLOWED_ORIGINS', '').split(',')
    if allowed_origins and allowed_origins[0]:
        CORS(app, origins=allowed_origins)
    else:
        CORS(app)
else:
    CORS(app)

SHOPIFY_STORE_URL = os.getenv('SHOPIFY_STORE_URL')
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN')
SHOPIFY_WEBHOOK_SECRET = os.getenv('SHOPIFY_WEBHOOK_SECRET', '')

RECONCILE_INTERVAL_SECONDS = 6 * 3600  # 6 hours
PERIOD_CACHE_TTL_SECONDS = 300          # 5 minutes

# ---------------------------------------------------------------------------
# In-memory state
# all_time_count: seeded from the API at startup, kept live via webhooks.
# period_cache: short-lived API results for non-all-time queries, invalidated
#               on every webhook event so stale counts aren't served.
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state: dict = {
    'all_time_count': None,
    'initialized': False,
    'last_reconciled': None,
    'webhooks_received': 0,
    'last_webhook_at': None,
    'period_cache': {},  # {period: {'count': int, 'expires_at': datetime}}
}


def validate_config() -> bool:
    missing = []
    if not SHOPIFY_STORE_URL:
        missing.append('SHOPIFY_STORE_URL')
    if not SHOPIFY_ACCESS_TOKEN:
        missing.append('SHOPIFY_ACCESS_TOKEN')
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        return False
    if not SHOPIFY_WEBHOOK_SECRET:
        logger.warning(
            "SHOPIFY_WEBHOOK_SECRET not set — incoming webhooks will not be verified. "
            "Set this to the signing secret from your Shopify webhook configuration."
        )
    return True


def get_date_range(period: str):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == 'today':
        return today_start.isoformat(), now.isoformat()
    if period == 'yesterday':
        start = today_start - timedelta(days=1)
        return start.isoformat(), today_start.isoformat()
    if period == 'this-week':
        start = today_start - timedelta(days=now.weekday())
        return start.isoformat(), now.isoformat()
    if period == 'last-week':
        this_week = today_start - timedelta(days=now.weekday())
        return (this_week - timedelta(days=7)).isoformat(), this_week.isoformat()
    if period == 'this-month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(), now.isoformat()
    if period == 'last-month':
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = first_this - timedelta(days=1)
        return last_month_end.replace(day=1).isoformat(), first_this.isoformat()
    if period == 'this-year':
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start.isoformat(), now.isoformat()
    if period == 'last-year':
        this_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return this_year.replace(year=this_year.year - 1).isoformat(), this_year.isoformat()
    return None, None  # all-time


def fetch_order_count_from_api(period: str = 'all-time'):
    """Call the Shopify Admin REST API to get an authoritative order count."""
    try:
        headers = {
            'X-Shopify-Access-Token': SHOPIFY_ACCESS_TOKEN,
            'Content-Type': 'application/json',
            'User-Agent': 'Shopify-Order-Counter/2.0',
        }
        url = f'https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/count.json?status=any'

        start_date, end_date = get_date_range(period)
        if start_date:
            url += f'&created_at_min={start_date}&created_at_max={end_date}'

        logger.info(f"Calling Shopify API for period={period}")
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            count = response.json().get('count', 0)
            logger.info(f"Shopify API returned count={count} for period={period}")
            return count

        logger.error(f"Shopify API error: HTTP {response.status_code}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Shopify API request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error calling Shopify API: {e}")
        return None


# ---------------------------------------------------------------------------
# Background services
# ---------------------------------------------------------------------------

def _initialize_counter():
    """Seed the in-memory all-time count from the Shopify API once at startup."""
    logger.info("Initializing order count from Shopify API...")
    count = fetch_order_count_from_api('all-time')
    if count is not None:
        with _lock:
            _state['all_time_count'] = count
            _state['initialized'] = True
            _state['last_reconciled'] = datetime.now(timezone.utc)
        logger.info(f"Counter initialized: all_time_count={count}")
    else:
        logger.error(
            "Could not initialize counter from Shopify API. "
            "Webhook increments will be counted relative to 0 until the next reconciliation."
        )


def _reconcile():
    """
    Fetch the authoritative all-time count from Shopify and correct any drift.
    Runs every 6 hours as a safety net — webhooks are the primary update path.
    """
    logger.info("Starting 6-hour reconciliation against Shopify API...")
    api_count = fetch_order_count_from_api('all-time')
    if api_count is None:
        logger.error("Reconciliation skipped: Shopify API unavailable")
        return

    with _lock:
        cached = _state['all_time_count']
        if cached != api_count:
            logger.warning(
                f"Drift detected — correcting: cached={cached}, api={api_count}"
            )
            _state['all_time_count'] = api_count
            _state['period_cache'].clear()
        else:
            logger.info(f"Reconciliation OK: count={api_count}, no drift")
        _state['last_reconciled'] = datetime.now(timezone.utc)


def _reconciliation_loop():
    """Daemon thread: sleep 6 hours then reconcile, indefinitely."""
    while True:
        time.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            _reconcile()
        except Exception as e:
            logger.error(f"Reconciliation loop error: {e}")


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def _verify_shopify_hmac(raw_body: bytes, signature: str) -> bool:
    """
    Verify Shopify's HMAC-SHA256 webhook signature.
    Returns True if no secret is configured (dev/testing mode).
    """
    if not SHOPIFY_WEBHOOK_SECRET:
        return True
    digest = hmac.new(
        SHOPIFY_WEBHOOK_SECRET.encode('utf-8'),
        raw_body,
        hashlib.sha256,
    ).digest()
    computed = base64.b64encode(digest).decode('utf-8')
    return hmac.compare_digest(computed, signature)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/webhooks/orders', methods=['POST'])
def handle_order_webhook():
    """
    Receive Shopify order webhooks to maintain a live order count.

    Register two webhooks in your Shopify admin pointing to this endpoint:
      - Topic: orders/create  → URL: https://<your-domain>/webhooks/orders
      - Topic: orders/delete  → URL: https://<your-domain>/webhooks/orders

    orders/cancelled is intentionally ignored: cancelled orders are still
    included in status=any queries, so the total count does not change.
    """
    raw_body = request.get_data()
    signature = request.headers.get('X-Shopify-Hmac-Sha256', '')

    if not _verify_shopify_hmac(raw_body, signature):
        logger.warning("Rejected webhook: HMAC signature mismatch")
        return jsonify({'error': 'Unauthorized'}), 401

    topic = request.headers.get('X-Shopify-Topic', '')
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}

    order_id = payload.get('id', 'unknown')
    logger.info(f"Webhook received: topic={topic}, order_id={order_id}")

    with _lock:
        if not _state['initialized']:
            # The startup thread hasn't finished yet; ignore and let reconciliation
            # correct the count once initialization completes.
            logger.warning("Webhook arrived before counter was initialized; ignored")
            return jsonify({'status': 'ignored', 'reason': 'not_initialized'}), 200

        if topic == 'orders/create':
            _state['all_time_count'] += 1
        elif topic == 'orders/delete':
            _state['all_time_count'] = max(0, _state['all_time_count'] - 1)
        # Any webhook event means period-based caches may now be stale
        _state['period_cache'].clear()
        _state['webhooks_received'] += 1
        _state['last_webhook_at'] = datetime.now(timezone.utc).isoformat()

    return jsonify({'status': 'ok'}), 200


@app.route('/api/orders/count')
def get_order_count():
    allowed_periods = [
        'today', 'yesterday', 'this-week', 'last-week',
        'this-month', 'last-month', 'this-year', 'last-year', 'all-time',
    ]
    period = request.args.get('period', 'all-time')
    if period not in allowed_periods:
        return jsonify({
            'success': False,
            'error': 'Invalid period parameter',
            'allowed_periods': allowed_periods,
            'timestamp': datetime.now().isoformat(),
        }), 400

    # all-time: serve directly from the webhook-maintained in-memory counter
    if period == 'all-time':
        with _lock:
            if _state['initialized'] and _state['all_time_count'] is not None:
                return jsonify({
                    'success': True,
                    'count': _state['all_time_count'],
                    'period': period,
                    'source': 'webhook_cache',
                    'timestamp': datetime.now().isoformat(),
                })
        # Counter not yet initialized — fall through to API below

    else:
        # Period-based: serve from cache if it's still fresh
        with _lock:
            cached = _state['period_cache'].get(period)
            if cached and datetime.now(timezone.utc) < cached['expires_at']:
                return jsonify({
                    'success': True,
                    'count': cached['count'],
                    'period': period,
                    'source': 'cache',
                    'timestamp': datetime.now().isoformat(),
                })

    # Fall back to Shopify API (all-time before init, or period cache miss)
    count = fetch_order_count_from_api(period)
    if count is None:
        return jsonify({
            'success': False,
            'error': 'Failed to fetch order count from Shopify API',
            'timestamp': datetime.now().isoformat(),
        }), 500

    with _lock:
        if period == 'all-time' and not _state['initialized']:
            # Bootstrap the counter from this opportunistic API call
            _state['all_time_count'] = count
            _state['initialized'] = True
            _state['last_reconciled'] = datetime.now(timezone.utc)
        elif period != 'all-time':
            _state['period_cache'][period] = {
                'count': count,
                'expires_at': datetime.now(timezone.utc) + timedelta(seconds=PERIOD_CACHE_TTL_SECONDS),
            }

    return jsonify({
        'success': True,
        'count': count,
        'period': period,
        'source': 'api',
        'timestamp': datetime.now().isoformat(),
    })


@app.route('/api/webhook/status')
def webhook_status():
    """Observability endpoint: current webhook state and reconciliation schedule."""
    with _lock:
        last_reconciled = _state['last_reconciled']
        next_in = None
        if last_reconciled:
            elapsed = (datetime.now(timezone.utc) - last_reconciled).total_seconds()
            next_in = max(0, int(RECONCILE_INTERVAL_SECONDS - elapsed))
        return jsonify({
            'initialized': _state['initialized'],
            'all_time_count': _state['all_time_count'],
            'webhooks_received': _state['webhooks_received'],
            'last_webhook_at': _state['last_webhook_at'],
            'last_reconciled': last_reconciled.isoformat() if last_reconciled else None,
            'next_reconciliation_in_seconds': next_in,
            'reconcile_interval_hours': RECONCILE_INTERVAL_SECONDS // 3600,
        })


@app.route('/health')
def health_check():
    config_valid = validate_config()
    status = {
        'status': 'healthy' if config_valid else 'unhealthy',
        'timestamp': datetime.now().isoformat(),
        'version': '2.0.0',
        'config': 'valid' if config_valid else 'invalid',
        'counter_initialized': _state['initialized'],
    }
    return jsonify(status), 200 if config_valid else 503


@app.route('/config/check')
def config_check():
    cfg = {
        'shopify_store_url': 'set' if SHOPIFY_STORE_URL else 'missing',
        'shopify_access_token': 'set' if SHOPIFY_ACCESS_TOKEN else 'missing',
        'shopify_webhook_secret': 'set' if SHOPIFY_WEBHOOK_SECRET else 'not set (webhooks unverified)',
        'timestamp': datetime.now().isoformat(),
    }
    required_ok = cfg['shopify_store_url'] == 'set' and cfg['shopify_access_token'] == 'set'
    if not required_ok:
        return jsonify({
            'success': False,
            'message': 'Missing required environment variables',
            'config': cfg,
            'help': {
                'required': ['SHOPIFY_STORE_URL', 'SHOPIFY_ACCESS_TOKEN'],
                'optional': ['SHOPIFY_WEBHOOK_SECRET', 'FLASK_ENV', 'PORT', 'ALLOWED_ORIGINS'],
            },
        }), 400
    return jsonify({'success': True, 'message': 'All required configuration is set', 'config': cfg})


@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'error': 'Endpoint not found', 'timestamp': datetime.now().isoformat()}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'success': False, 'error': 'Internal server error', 'timestamp': datetime.now().isoformat()}), 500


# ---------------------------------------------------------------------------
# Startup: seed counter from API, then keep reconciliation loop running.
# Works for Gunicorn, uWSGI, and direct `python app.py`.
# In Flask debug mode with the reloader, both the parent and child processes
# import this module; daemon threads are harmless in both.
# ---------------------------------------------------------------------------
if validate_config():
    threading.Thread(target=_initialize_counter, daemon=True, name='counter-init').start()
    threading.Thread(target=_reconciliation_loop, daemon=True, name='counter-reconcile').start()


if __name__ == '__main__':
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/assets', exist_ok=True)

    port = int(os.getenv('PORT', 5010))
    is_production = os.getenv('FLASK_ENV') == 'production'

    if is_production:
        logger.info("Starting in PRODUCTION mode...")
        app.run(debug=False, host='0.0.0.0', port=port)
    else:
        logger.info("Starting in DEVELOPMENT mode...")
        app.run(debug=True, host='0.0.0.0', port=port)
