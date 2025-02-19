#!/usr/bin/env bash

# build for amd64
docker buildx build --platform linux/amd64 \
  -t gcr.io/poker-451119/backend:v1 \
  --push .
docker run -d --env-file .env.prod -p 8000:8000 gcr.io/poker-451119/backend:v1
