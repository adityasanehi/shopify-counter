from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import requests
import os
import logging
from datetime import datetime, timedelta, timezone

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configure CORS properly for production
if os.getenv('FLASK_ENV') == 'production':
    allowed_origins = os.getenv('ALLOWED_ORIGINS', '').split(',')
    if allowed_origins and allowed_origins[0]:
        CORS(app, origins=allowed_origins)
    else:
        CORS(app)
else:
    # Development mode - allow all origins
    CORS(app)

# Shopify configuration - MUST be provided via environment variables
SHOPIFY_STORE_URL = os.getenv('SHOPIFY_STORE_URL')
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN')

# Validate required environment variables at startup
def validate_config():
    """Validate required configuration"""
    missing = []
    if not SHOPIFY_STORE_URL:
        missing.append('SHOPIFY_STORE_URL')
    if not SHOPIFY_ACCESS_TOKEN:
        missing.append('SHOPIFY_ACCESS_TOKEN')
    
    if missing:
        error_msg = f"Missing required environment variables: {', '.join(missing)}"
        logger.error(error_msg)
        logger.error("Please set these environment variables and restart the application.")
        logger.error("Example: SHOPIFY_STORE_URL=your-store.myshopify.com")
        logger.error("Example: SHOPIFY_ACCESS_TOKEN=shpat_your_token_here")
        return False
    return True


def get_date_range(period):
    """Get date range based on period filter"""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if period == 'today':
        start_date = today_start
        end_date = now
    elif period == 'yesterday':
        start_date = today_start - timedelta(days=1)
        end_date = today_start
    elif period == 'this-week':
        days_since_monday = now.weekday()
        start_date = today_start - timedelta(days=days_since_monday)
        end_date = now
    elif period == 'last-week':
        days_since_monday = now.weekday()
        this_week_start = today_start - timedelta(days=days_since_monday)
        start_date = this_week_start - timedelta(days=7)
        end_date = this_week_start
    elif period == 'this-month':
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now
    elif period == 'last-month':
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = first_of_this_month - timedelta(days=1)
        start_date = last_month.replace(day=1)
        end_date = first_of_this_month
    elif period == 'this-year':
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now
    elif period == 'last-year':
        this_year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        start_date = this_year_start.replace(year=this_year_start.year - 1)
        end_date = this_year_start
    else:  # 'all-time' or default
        return None, None
    
    return start_date.isoformat(), end_date.isoformat()


def get_shopify_orders(period='all-time'):
    """Fetch order count from Shopify Admin API with optional period filter"""
    try:
        headers = {
            'X-Shopify-Access-Token': SHOPIFY_ACCESS_TOKEN,
            'Content-Type': 'application/json',
            'User-Agent': 'Shopify-Order-Counter/1.0'
        }
        
        # Build URL with filters
        url = f'https://{SHOPIFY_STORE_URL}/admin/api/2023-10/orders/count.json?status=any'
        
        # Add date filters if not all-time
        start_date, end_date = get_date_range(period)
        if start_date and end_date:
            url += f'&created_at_min={start_date}&created_at_max={end_date}'
            
        logger.info(f"Fetching orders for period: {period}")
        if start_date:
            logger.info(f"Date range: {start_date} to {end_date}")
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            count = data.get('count', 0)
            logger.info(f"Successfully fetched order count: {count}")
            return count
        else:
            logger.error(f"Shopify API error: {response.status_code}")
            # Don't log the full response text in production to avoid exposing sensitive data
            return 0
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return None


@app.route('/')
def index():
    """Serve the main counter page"""
    return render_template('index.html')


@app.route('/api/orders/count')
def get_order_count():
    """API endpoint to get current order count"""
    # Validate period parameter
    allowed_periods = ['today', 'yesterday', 'this-week', 'last-week', 
                      'this-month', 'last-month', 'this-year', 'last-year', 'all-time']
    
    period = request.args.get('period', 'all-time')
    if period not in allowed_periods:
        return jsonify({
            'success': False,
            'error': 'Invalid period parameter',
            'allowed_periods': allowed_periods,
            'timestamp': datetime.now().isoformat()
        }), 400
    
    count = get_shopify_orders(period)
    
    if count is not None:
        return jsonify({
            'success': True,
            'count': count,
            'period': period,
            'timestamp': datetime.now().isoformat()
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Failed to fetch order count from Shopify API',
            'timestamp': datetime.now().isoformat()
        }), 500


@app.route('/health')
def health_check():
    """Health check endpoint with configuration status"""
    health_status = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0'
    }
    
    # Check if configuration is valid
    config_valid = validate_config()
    health_status['config'] = 'valid' if config_valid else 'invalid'
    
    if not config_valid:
        health_status['status'] = 'unhealthy'
        return jsonify(health_status), 503
    
    return jsonify(health_status)


@app.route('/config/check')
def config_check():
    """Configuration check endpoint"""
    config_status = {
        'shopify_store_url': 'set' if SHOPIFY_STORE_URL else 'missing',
        'shopify_access_token': 'set' if SHOPIFY_ACCESS_TOKEN else 'missing',
        'timestamp': datetime.now().isoformat()
    }
    
    all_set = all(v == 'set' for v in [config_status['shopify_store_url'], config_status['shopify_access_token']])
    
    if not all_set:
        return jsonify({
            'success': False,
            'message': 'Missing required environment variables',
            'config': config_status,
            'help': {
                'required_variables': ['SHOPIFY_STORE_URL', 'SHOPIFY_ACCESS_TOKEN'],
                'example': {
                    'SHOPIFY_STORE_URL': 'your-store.myshopify.com',
                    'SHOPIFY_ACCESS_TOKEN': 'shpat_your_token_here'
                }
            }
        }), 400
    
    return jsonify({
        'success': True,
        'message': 'All required configuration is set',
        'config': config_status
    })


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({
        'success': False,
        'error': 'Endpoint not found',
        'timestamp': datetime.now().isoformat()
    }), 404


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({
        'success': False,
        'error': 'Internal server error',
        'timestamp': datetime.now().isoformat()
    }), 500


if __name__ == '__main__':
    # Create directories if they don't exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/assets', exist_ok=True)
    
    # Check configuration before starting
    if not validate_config():
        logger.error("Cannot start application due to missing configuration.")
        logger.error("Please set the required environment variables and try again.")
        exit(1)
    
    # Production vs development configuration
    is_production = os.getenv('FLASK_ENV') == 'production'
    port = int(os.getenv('PORT', 5010))
    
    if is_production:
        logger.info("Starting Shopify Order Counter in PRODUCTION mode...")
        logger.info("Configuration validated successfully")
        app.run(debug=False, host='0.0.0.0', port=port)
    else:
        logger.info("Starting Shopify Order Counter in DEVELOPMENT mode...")
        logger.info(f"Store URL: {SHOPIFY_STORE_URL}")
        app.run(debug=True, host='0.0.0.0', port=port)