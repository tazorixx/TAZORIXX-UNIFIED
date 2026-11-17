# Optional self-hosted frontend for fully free commercial use on one VM.
FROM node:24-bookworm-slim
WORKDIR /web
COPY package.json .
RUN npm install --no-audit --no-fund
COPY . .
ARG STUDIO_ORIGIN
ARG NEXT_PUBLIC_GATEWAY_URL
ENV STUDIO_ORIGIN=${STUDIO_ORIGIN}
ENV NEXT_PUBLIC_GATEWAY_URL=${NEXT_PUBLIC_GATEWAY_URL}
RUN npm run build
EXPOSE 3000
CMD ["npm","run","start"]
