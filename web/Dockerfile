# Build context is the repository root.
FROM node:22-slim AS build
WORKDIR /repo/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ARG VITE_TEST_HOOKS=0
ENV VITE_TEST_HOOKS=$VITE_TEST_HOOKS
RUN npm run build

FROM nginx:1.27-alpine
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/web/dist /usr/share/nginx/html
