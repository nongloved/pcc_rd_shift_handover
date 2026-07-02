FROM node:20-slim

ENV NODE_ENV=production

WORKDIR /app

COPY package*.json ./
RUN npm install --only=production

COPY server.js ./
COPY public/ ./public/

CMD ["node", "server.js"]
