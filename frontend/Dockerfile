# Development Dockerfile for Shizuha Mail Frontend
FROM node:20-alpine

WORKDIR /app

# Create packages directory (will be volume-mounted in dev)
RUN mkdir -p /packages

# Copy package files
COPY package*.json ./
COPY vendor/ ./vendor/

# Install dependencies
RUN npm ci --ignore-scripts --no-audit --no-fund

# Copy source files
COPY . .

# Expose port
EXPOSE 80

# Start dev server
CMD ["npm", "run", "dev"]
