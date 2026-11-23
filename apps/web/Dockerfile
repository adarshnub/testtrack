FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.json ./
COPY prisma ./prisma
COPY apps/web/package.json ./apps/web/package.json
RUN pnpm install --frozen-lockfile
COPY apps/web ./apps/web
COPY packages ./packages
RUN pnpm build && mkdir -p /app/var/private && chown -R node:node /app/var /app/apps/web/.next
USER node
ENV NODE_ENV=production PORT=3000
EXPOSE 3000
CMD ["pnpm", "start"]
