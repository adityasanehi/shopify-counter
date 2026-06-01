# Shopify Order Counter

A real-time order counter that displays your Shopify store's total order count with a sleek flip-counter interface.

![Docker Pulls](https://img.shields.io/badge/docker-ghcr.io-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## 🚀 Quick Start

### Using Docker

```bash
docker run -p 5010:5010 \
  -e SHOPIFY_STORE_URL=your-store.myshopify.com \
  -e SHOPIFY_ACCESS_TOKEN=shpat_your_token_here \
  ghcr.io/adityasanehi/shopify-counter:latest
```

### Using Docker Compose

```yaml
version: '3.8'
services:
  shopify-counter:
    image: ghcr.io/adityasanehi/shopify-counter:latest
    ports:
      - "5010:5010"
    environment:
      - SHOPIFY_STORE_URL=your-store.myshopify.com
      - SHOPIFY_ACCESS_TOKEN=shpat_your_token_here
      - FLASK_ENV=production
    restart: unless-stopped
```

### Using Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: shopify-counter
spec:
  replicas: 1
  selector:
    matchLabels:
      app: shopify-counter
  template:
    metadata:
      labels:
        app: shopify-counter
    spec:
      containers:
      - name: shopify-counter
        image: ghcr.io/adityasanehi/shopify-counter:latest
        ports:
        - containerPort: 5010
        env:
        - name: SHOPIFY_STORE_URL
          value: "your-store.myshopify.com"
        - name: SHOPIFY_ACCESS_TOKEN
          valueFrom:
            secretKeyRef:
              name: shopify-secrets
              key: access-token
```

## ⚙️ Configuration

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SHOPIFY_STORE_URL` | Your Shopify store URL | `your-store.myshopify.com` |
| `SHOPIFY_ACCESS_TOKEN` | Your Shopify private app access token | `shpat_abc123...` |

### Optional Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SHOPIFY_WEBHOOK_SECRET` | Webhook signing secret from Shopify Admin | *(unverified if not set)* |
| `FLASK_ENV` | Environment mode | `development` |
| `PORT` | Port to run the application | `5010` |
| `ALLOWED_ORIGINS` | CORS allowed origins (comma-separated) | All origins allowed |

## 🔧 Shopify Setup

### 1. Create a Private App

1. Go to your Shopify Admin → **Apps** → **App and sales channel settings**
2. Click **"Develop apps"** → **"Create an app"**
3. Name your app (e.g., "Order Counter")
4. Go to **"Configuration"** → **"Admin API integration"**
5. Enable **"Orders"** with **read access**
6. Save and install the app
7. Copy your **Access Token**

### 2. Required Permissions

Your Shopify private app needs these permissions:
- **Orders**: `read_orders`

### 3. Register Webhooks (Recommended)

Webhooks keep the all-time order count updated instantly without any API polling.

1. Go to **Shopify Admin → Settings → Notifications → Webhooks**
2. Create two webhooks pointing to your public app URL:

| Topic | URL |
|-------|-----|
| `orders/create` | `https://your-domain/webhooks/orders` |
| `orders/delete` | `https://your-domain/webhooks/orders` |

3. Copy the **Signing secret** shown after creating the webhooks
4. Set it as `SHOPIFY_WEBHOOK_SECRET` in your environment

> **Note on `orders/cancelled`:** You do *not* need a webhook for cancellations.
> Cancelled orders are still counted under `status=any`, so the total does not change.

## 📊 Features

- ⚡ **Webhook-driven updates** - All-time count updated instantly on every new order; no Shopify API polling
- 🔄 **6-hour reconciliation** - Background safety check corrects any drift between the cached count and the Shopify API
- 🗄️ **Period caching** - Period-based counts (today, this week, etc.) are cached for 5 minutes and invalidated on each webhook event
- 📱 **Responsive design** - Works on mobile and desktop
- 🎯 **Period filtering** - Today, this week, this month, all-time, etc.
- 🛡️ **Error handling** - Graceful handling of API failures
- 🔍 **Health checks** - Built-in health check endpoint

## 🌐 API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Main counter interface |
| `GET /api/orders/count?period=all-time` | JSON API for order count |
| `POST /webhooks/orders` | Shopify webhook receiver (orders/create, orders/delete) |
| `GET /api/webhook/status` | Webhook state and reconciliation schedule |
| `GET /health` | Health check endpoint |
| `GET /config/check` | Configuration validation |

### Count response — `source` field

The `/api/orders/count` response includes a `source` field so you can tell where the number came from:

| Value | Meaning |
|-------|---------|
| `webhook_cache` | Served from the in-memory counter maintained by webhooks |
| `cache` | Served from a short-lived period cache (< 5 min old) |
| `api` | Fetched live from the Shopify API (first load or cache miss) |

### Period Filters

- `today` - Orders from today
- `yesterday` - Orders from yesterday  
- `this-week` - Orders from this week
- `last-week` - Orders from last week
- `this-month` - Orders from this month
- `last-month` - Orders from last month
- `this-year` - Orders from this year
- `last-year` - Orders from last year
- `all-time` - All orders (default)

## 🏥 Health Checks

The container includes built-in health checks:

```bash
# Check if the application is running
curl http://localhost:5010/health

# Verify configuration
curl http://localhost:5010/config/check
```

## 🔒 Security

- No hardcoded credentials in the container
- Runs as non-root user
- Validates all environment variables at startup
- Proper error handling without information leakage
- CORS configuration for production environments

## 🐳 Container Details

- **Base Image**: `python:3.11-slim`
- **Port**: `5010`
- **User**: Non-root user (`appuser`)
- **Health Check**: `/health` endpoint every 30s
- **Multi-arch**: `linux/amd64`, `linux/arm64`

## 📝 Example Response

```json
{
  "success": true,
  "count": 1337,
  "period": "all-time",
  "timestamp": "2025-08-06T10:30:00.000Z"
}
```


## 🏷️ Version Tags

You can use specific versions for production stability:

```bash
# Use latest (updated with each push to main)
docker pull ghcr.io/adityasanehi/shopify-counter:latest

# Use specific version (recommended for production)
docker pull ghcr.io/adityasanehi/shopify-counter:v1.0.0

# Use major version (gets patch updates)
docker pull ghcr.io/adityasanehi/shopify-counter:1
```

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

- **Issues**: [GitHub Issues](https://github.com/adityasanehi/shopify-counter/issues)
- **Documentation**: [Wiki](https://github.com/adityasanehi/shopify-counter/wiki)
- **Docker Hub**: [ghcr.io/adityasanehi/shopify-counter](https://github.com/adityasanehi/shopify-counter/pkgs/container/shopify-counter)

## 🔄 Updates

The container is automatically built and updated via GitHub Actions when changes are pushed to the main branch.