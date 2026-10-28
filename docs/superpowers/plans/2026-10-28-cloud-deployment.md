# Cloud Deployment Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for this bounded implementation.

**Goal:** Ignore private environment files and prepare scripts for a standalone cloud DSH service, the existing frontend, and Nginx.

**Architecture:** Linux runs the pinned DSH CLI web profile on loopback, managed by systemd and authenticated with DEEPSEEK_API_KEY. Nginx terminates HTTPS, serves frontend assets and proxies /dsh/ HTTP/WebSocket traffic. Existing Windows game workers remain separate and are not installed on Linux.

**Tech stack:** Python deployment client, SSH/SFTP, Bash, Node 24+, systemd, Nginx.

**Spec:** Current user request and clarification that DSH should work without its desktop application using an API key. Prepare locally; no cloud changes requested for this execution.

## Constraints and review focus

- Preserve all existing uncommitted changes and the supplied credentials file.
- Ignore .env variants at every depth while keeping .env.example templates trackable.
- Never print credentials, put them in shell commands, or include them in the release archive.
- DSH launch token and model API key are separate; retain browser authentication and trusted-host checks.
- TLS is required for the public entry; preserve Host, strip /dsh/, forward WebSockets and rewrite cookie scope.
- Require explicit deployment mode, known SSH host keys, a specified SSH user/domain and server prerequisites.
- Retain previous releases and configuration for failure recovery. Do not alter unrelated Nginx sites.

## Tasks

- [x] Add ignore rules and confirm real environment files are untracked.
- [x] Verify CLI and API-key support against the installed CLI and official upstream documentation.
- [ ] Test credential parsing, config injection rejection, and release packaging before implementation.
- [ ] Implement local prepare/apply script, Linux installer, systemd and Nginx templates.
- [ ] Document cloud setup, HTTPS, API keys, connection token, workspace selection and Windows-tool boundary.
- [ ] Run local tests, build/package checks, Bash/config syntax checks and independent review; explicitly report that live cloud deployment was not run.
