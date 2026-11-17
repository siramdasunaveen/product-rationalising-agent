# Product Rationalisation Agent - standalone UI (static build served by nginx, /api proxied to the API).
#   docker build -t pra-web ./frontend
#   docker run -p 8080:8080 -e API_UPSTREAM=http://api:8000 pra-web
FROM node:20-alpine AS build
WORKDIR /src
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
ENV API_UPSTREAM=http://api:8000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
