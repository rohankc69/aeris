FROM node:24-alpine AS build
WORKDIR /app
RUN corepack enable
COPY dashboard/package.json dashboard/pnpm-lock.yaml dashboard/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY dashboard/ ./
ARG NEXT_PUBLIC_AERIS_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_AERIS_API_URL=${NEXT_PUBLIC_AERIS_API_URL}
RUN pnpm build

FROM node:24-alpine
WORKDIR /app
RUN corepack enable
COPY --from=build /app ./
EXPOSE 3000
CMD ["pnpm", "start"]
