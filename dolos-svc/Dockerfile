FROM node:20-slim
# node-gyp needs python3 + a C++ toolchain to build the native tree-sitter
# bindings @dodona/dolos-parsers depends on — node:20-slim has neither.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 make g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY package.json .
RUN npm install --omit=dev
COPY server.js .
EXPOSE 8801
CMD ["node", "server.js"]
