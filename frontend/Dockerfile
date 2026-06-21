FROM node:20-alpine AS build

WORKDIR /frontend
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

RUN apk add --no-cache curl

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /frontend/dist /usr/share/nginx/html

HEALTHCHECK --interval=10s --timeout=5s --retries=20 CMD curl --fail --silent http://localhost:8080/health || exit 1
