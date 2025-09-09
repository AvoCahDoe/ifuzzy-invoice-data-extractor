FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build:ssr

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production \
    PORT=4000 \
    API_BASE_URL=http://backend:8000
COPY --from=build /app /app
EXPOSE 4000
CMD ["npm", "run", "serve:ssr"]
