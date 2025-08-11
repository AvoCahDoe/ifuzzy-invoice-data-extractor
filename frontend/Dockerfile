FROM node:20-alpine AS builder

WORKDIR /app

COPY . .

RUN npm install --legacy-peer-deps
RUN npm run build:ssr

FROM node:20-alpine

WORKDIR /app

COPY --from=builder /app/dist /app/dist
COPY --from=builder /app/package.json .

RUN npm install --only=prod --legacy-peer-deps

EXPOSE 4000

CMD ["node", "dist/frontend/server/server.mjs"]
