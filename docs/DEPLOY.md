# Running devmem on a server

`devmem-app ui` is a local app by default: it listens on 127.0.0.1 and trusts whoever can reach it. Started with a
network address it switches to **deployed mode**:

```
DEVMEM_TOKEN=<long random string> devmem-app ui --host 0.0.0.0 --port 8765 --no-open
```

or with the image in this repo:

```
docker build -t devmem .
docker run -p 8765:8765 -v devmem-data:/data -e DEVMEM_TOKEN=<long random string> devmem
```

The image has not been built in CI yet; treat it as a starting point and report what breaks.

## What changes in deployed mode

| | Local | Deployed |
| --- | --- | --- |
| Access | anyone on the machine | every `/api` call needs the access token: the sign-in page stores it in an HttpOnly, SameSite=Strict cookie; scripts send `Authorization: Bearer <token>` |
| Token | none | `DEVMEM_TOKEN`; if it is unset and `--host` is not local, one is generated and printed at start |
| Host header | must be localhost | any, unless `DEVMEM_ALLOWED_HOSTS=devmem.example.com,...` narrows it |
| Adding a repo | upload, Git URL, or a path on this computer | upload or Git URL; a path on the server only with `DEVMEM_ALLOW_PATHS=1` |
| "Open files in" editor links | shown | hidden (the files are not on your machine) |

Put it behind HTTPS (a reverse proxy or your platform's TLS). The cookie is marked `Secure` when the request arrives
over HTTPS or with `X-Forwarded-Proto: https`; set `FORWARDED_ALLOW_IPS` to your proxy's address so that header is trusted.
One token means one shared login: there are no per-user accounts or roles.

## Adding repos without a path

**Upload** (Repos page): drop a project folder or a `.zip` / `.tar.gz`. A folder is packed in the browser; `.git`,
`node_modules`, virtualenvs and files over 5 MB are left out. The server unpacks it under `$DEVMEM_HOME/uploads/<name>`,
skipping anything that would land outside that folder, links, and `.git` (devmem runs git in repos, and a crafted
`.git/config` can run commands). Limits: `DEVMEM_MAX_UPLOAD_MB` (default 300) for the archive and
`DEVMEM_MAX_UNPACKED_MB` (default 2000) for what it unpacks to. Uploading again under the same name replaces the code and
keeps `.devmem/` (lessons, rules, descriptions).

An uploaded copy has no git history, so branch switching, co-change insights and "publish also commits" do not apply to
it. After **Publish**, use *Download knowledge (.zip)* in the row menu and unpack it at the root of the real repo: it
holds `.devmem/`, `AGENTS.md` and the Copilot instructions file. Commit those as usual.

**Git URL**: cloned on the server with the server's git credentials (an SSH key or credential helper you provide, for
example a mounted deploy key). Cloned repos keep their history and can be re-indexed when HEAD moves.

## State

Everything is under `DEVMEM_HOME` (`/data` in the image): `workspace.json`, `uploads/`, `clones/`, the library-docs store,
the hub clone and `settings.json` (AI provider and key, mode 0600). Mount it as a volume.

## AI on a server

Set it on the **AI setup** page after signing in, or pin it with environment variables, which win over the page:
`DEVMEM_AI_PROVIDER` (`anthropic`, `openai`, `gemini`, `vertex`), `DEVMEM_AI_KEY`, `DEVMEM_AI_MODEL`, and for
OpenAI-compatible servers `DEVMEM_AI_BASE_URL`. See `docs/AI.md`.

## SonarQube on a server

The devmem server calls your SonarQube server itself, so it needs a network route to it. Set the connection on the
**Integrations** page or with `DEVMEM_SONAR_URL` and `DEVMEM_SONAR_TOKEN`. The token is kept in `settings.json`
under `DEVMEM_HOME`; synced findings are written to `.devmem/sonar.json` in each repo.

## Long operations

Onboarding (path, Git URL or upload), re-indexing, branch switches, publishing, code-health runs, SonarQube sync, Modernize
scans and accuracy self-checks accept `?background=1`. The request then returns `202 {"job": "<id>"}` at once and the work
continues on the server; `GET /api/jobs/<id>` reports `running`, `done` (with the result) or `failed` (with the status and
message the endpoint would have returned). The web UI always uses this, so a reverse proxy's request timeout cannot cut off
indexing of a large repo, and leaving the page does not stop it. Only one indexing or publishing job runs per repo at a time;
a second one is refused with 409. Jobs live in the server process: a restart forgets them (the work in progress stops, and
the index is left as it was, since it is only replaced when a run completes).
