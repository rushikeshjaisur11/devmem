# devmem web app for a server: sign-in with an access token, repos onboarded by upload or Git URL.
#   docker build -t devmem .
#   docker run -p 8765:8765 -v devmem-data:/data -e DEVMEM_TOKEN=<long random string> devmem
# See docs/DEPLOY.md.
FROM python:3.12-slim
# git: cloning by URL and history insights. Node.js: npx runs the SCIP indexers (type-resolved edges).
RUN apt-get update && apt-get install -y --no-install-recommends git nodejs npm && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY packages packages
# both in one command, so the local `devmem` satisfies devmem-app's dependency (the PyPI name `devmem` is another project)
RUN pip install --no-cache-dir ./packages/devmem ./packages/devmem-app
ENV DEVMEM_HOME=/data DEVMEM_HOST=0.0.0.0 PORT=8765
VOLUME /data
EXPOSE 8765
CMD ["devmem-app", "ui", "--no-open"]
