# ESF Dash RAG

A modern RAG (Retrieval-Augmented Generation) system with Auth0 authentication, built with React and FastAPI.

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose
- Node.js (v16 or higher)
- Python 3.8+
- Auth0 account
- OpenAI API key
- AWS credentials (for S3 and OpenSearch)

### Local Development Setup

1. **Clone the repository**

```bash
git clone <your-repo-url>
cd esf-dash-rag
```

2. **Set up environment variables**

```bash
# Copy the example env file
cp .env.example .env

# Edit .env with your credentials:
# - AWS_ACCESS_KEY
# - AWS_SECRET_KEY
# - AWS_REGION
# - S3_BUCKET
# - OPENSEARCH_ENDPOINT
# - OPENAI_API_KEY
# - AUTH0_DOMAIN
# - AUTH0_AUDIENCE
```

3. **Start the application**

Using Docker (recommended):

```bash
# Build and start all services
docker-compose up -d --build

# View logs
docker-compose logs -f
```

Manual setup (for development):

Backend:

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
cd backend
pip install -r requirements.txt

# Start the backend server
uvicorn main:app --reload --port 8000
```

Frontend:

```bash
# Install dependencies
cd frontend
npm install

# Start the development server
npm start
```

4. **Access the application**

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Documentation: http://localhost:8000/docs

## 🔧 Development

### Project Structure

```
esf-dash-rag/
├── frontend/           # React application
│   ├── src/           # Source code
│   └── public/        # Static files
├── backend/           # FastAPI application
│   ├── main.py       # Main application file
│   ├── models.py     # Database models
│   └── utils/        # Utility functions
└── nginx/            # Nginx configuration
```

### Available Scripts

Frontend:

```bash
npm start    # Start development server
npm test     # Run tests
npm run build # Build for production
```

Backend:

```bash
uvicorn main:app --reload  # Start development server
python -m pytest           # Run tests
```

Docker:

```bash
docker-compose up -d      # Start all services
docker-compose down       # Stop all services
docker-compose logs -f    # View logs
```

## 🔒 Authentication

This project uses Auth0 for authentication. To set up Auth0:

1. Create a new Single Page Application in Auth0
2. Configure the following URLs in Auth0:
   - Allowed Callback URLs: `http://localhost:3000`
   - Allowed Logout URLs: `http://localhost:3000`
   - Allowed Web Origins: `http://localhost:3000`
3. Create an API with identifier: `https://esf-dash-rag-api`
4. Update your `.env` file with Auth0 credentials

## 📚 API Documentation

The API documentation is available at:

- Local: http://localhost:8000/docs
- Production: https://your-domain.com/api/docs

## 🚀 Deployment

For production deployment instructions, see [DEPLOYMENT.md](DEPLOYMENT.md)

# ESF Dash RAG - Secure Deployment Guide

A secure, containerized RAG (Retrieval-Augmented Generation) system with Auth0 authentication, ready for AWS EC2 deployment.

## Architecture Overview

- **Frontend**: React application with Auth0 authentication
- **Backend**: FastAPI with JWT token validation
- **Reverse Proxy**: Nginx with SSL termination and rate limiting
- **Databases**: SQLite databases for local data storage
- **External Services**: AWS S3, OpenSearch, OpenAI API

## Prerequisites

- Docker and Docker Compose installed
- AWS EC2 instance (recommended: t3.large or larger)
- Domain name pointed to your EC2 instance
- SSL certificates (Let's Encrypt recommended)
- Auth0 account and application configured
- AWS credentials for S3 and OpenSearch
- OpenAI API key

## Security Features

- ✅ Auth0 authentication for all routes
- ✅ JWT token validation on backend
- ✅ HTTPS enforcement with SSL
- ✅ Rate limiting on API endpoints
- ✅ Security headers (HSTS, CSP, etc.)
- ✅ Non-root container execution
- ✅ Environment variable configuration

## Deployment Steps

### 1. EC2 Instance Setup

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Add current user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Clone and Configure

```bash
# Clone the repository
git clone <your-repo-url>
cd esf-dash-rag

# Copy environment template
cp .env.example .env

# Edit .env with your actual values
nano .env
```

### 3. SSL Certificate Setup

Create SSL certificates directory:

```bash
mkdir -p nginx/ssl
```

Option A - Let's Encrypt (recommended):

```bash
# Install certbot
sudo apt-get install certbot

# Generate certificates
sudo certbot certonly --standalone -d your-domain.com

# Copy certificates
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem nginx/ssl/cert.pem
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem nginx/ssl/key.pem
sudo chown $USER:$USER nginx/ssl/*
```

Option B - Self-signed (development only):

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/key.pem \
  -out nginx/ssl/cert.pem
```

### 4. Update Configuration

Update the frontend build environment:

```bash
# Edit docker-compose.yml to set REACT_APP_API_URL to your domain
nano docker-compose.yml
```

Update CORS origins in backend:

```python
# Edit backend/main.py to include your domain
origins = [
    "https://your-domain.com",
    "http://localhost:3000",  # for local development
]
```

### 5. Deploy

```bash
# Build and start all services
docker-compose up -d --build

# Check logs
docker-compose logs -f

# Verify health
curl https://your-domain.com/health
```

## Environment Variables

Create a `.env` file with:

```env
# AWS Configuration
AWS_ACCESS_KEY=your_aws_access_key
AWS_SECRET_KEY=your_aws_secret_key
AWS_REGION=your_aws_region
S3_BUCKET=your_s3_bucket_name

# OpenSearch Configuration
OPENSEARCH_ENDPOINT=your_opensearch_endpoint

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key

# Auth0 Configuration
AUTH0_DOMAIN=dev-lzz2sk107uunqvja.us.auth0.com
AUTH0_AUDIENCE=https://esf-dash-rag-api

# Frontend Configuration
REACT_APP_API_URL=https://your-domain.com/api
REACT_APP_AUTH0_AUDIENCE=https://esf-dash-rag-api
```

## Auth0 Configuration

1. Create a new Single Page Application in Auth0
2. Configure allowed callback URLs: `https://your-domain.com`
3. Configure allowed logout URLs: `https://your-domain.com`
4. Configure allowed web origins: `https://your-domain.com`
5. Create an API in Auth0 with identifier: `https://esf-dash-rag-api`
6. Enable RBAC and add necessary scopes

## Maintenance

### Update Application

```bash
git pull origin main
docker-compose down
docker-compose up -d --build
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f backend
docker-compose logs -f frontend
docker-compose logs -f nginx
```

### Backup Databases

```bash
# Create backup directory
mkdir -p backups

# Backup all databases
for db in backend/*.db; do
  cp "$db" "backups/$(basename $db).$(date +%Y%m%d_%H%M%S)"
done
```

### SSL Certificate Renewal

Set up a cron job for automatic renewal:

```bash
sudo crontab -e

# Add this line
0 0 1 * * certbot renew --quiet && cp /etc/letsencrypt/live/your-domain.com/fullchain.pem /path/to/nginx/ssl/cert.pem && cp /etc/letsencrypt/live/your-domain.com/privkey.pem /path/to/nginx/ssl/key.pem && docker-compose restart nginx
```

## Security Best Practices

1. **Regular Updates**: Keep all dependencies and base images updated
2. **Secrets Management**: Never commit .env files or secrets to git
3. **Firewall Rules**: Configure AWS Security Group to only allow ports 80, 443, and 22 (SSH)
4. **Monitoring**: Set up CloudWatch or similar for monitoring
5. **Backups**: Regular automated backups of databases and user data
6. **Rate Limiting**: Adjust rate limits in nginx config based on your needs

## Troubleshooting

### Backend not accessible

- Check Docker logs: `docker-compose logs backend`
- Verify environment variables are set correctly
- Ensure Auth0 configuration matches

### Frontend authentication issues

- Verify Auth0 domain and client ID
- Check browser console for errors
- Ensure callback URLs are configured in Auth0

### SSL certificate issues

- Verify certificate files exist in nginx/ssl/
- Check certificate permissions
- Ensure domain DNS is properly configured

## Support

For issues or questions:

1. Check application logs
2. Verify all environment variables
3. Ensure all external services (AWS, Auth0, OpenAI) are properly configured
4. Review security group and firewall settings
