# GitHub Actions — Secrets & Environments Setup

## 1. GitHub Environments

Create two environments in: **Settings → Environments**

| Environment | Protection rules |
|---|---|
| `dev`  | No approval required. Auto-deploys on push to `main`. |
| `prod` | Require 1 reviewer approval. Optional wait timer (5 min). |

---

## 2. Required Secrets

Add these in **Settings → Secrets and variables → Actions**.

### Shared (both environments)

| Secret | Description | Example |
|---|---|---|
| `CF_API_URL` | Cloud Foundry API endpoint | `https://api.cf.eu20.hana.ondemand.com` |
| `CF_USERNAME` | BTP login email | `user@company.com` |
| `CF_PASSWORD` | BTP login password | — |
| `CF_ORG` | CF organisation name | `mycompany-trial` |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook (optional) | `https://hooks.slack.com/...` |
| `ANTHROPIC_API_KEY_TEST` | Anthropic key for integration tests | `sk-ant-...` |

### Dev environment secrets

| Secret | Description |
|---|---|
| `CF_SPACE_DEV` | CF space name for dev | `dev` |
| `CF_DOMAIN_DEV` | CF app domain for dev | `cfapps.eu20.hana.ondemand.com` |
| `DATASPHERE_BASE_URL_DEV` | Datasphere tenant URL (dev) | |
| `DATASPHERE_SPACE_ID_DEV` | Datasphere space ID (dev) | |
| `DATASPHERE_TOKEN_URL_DEV` | OAuth2 token URL | |
| `DATASPHERE_CLIENT_ID_DEV` | OAuth2 client ID | |
| `DATASPHERE_CLIENT_SECRET_DEV` | OAuth2 client secret | |
| `SAC_TENANT_URL_DEV` | SAC tenant URL (dev) | |
| `SAC_TOKEN_URL_DEV` | SAC OAuth2 token URL | |
| `SAC_CLIENT_ID_DEV` | SAC client ID | |
| `SAC_CLIENT_SECRET_DEV` | SAC client secret | |
| `AICORE_DEPLOYMENT_ID_DEV` | AI Core deployment ID (dev) | |

### Prod environment secrets

Same as Dev but with `_PROD` suffix:

| Secret |
|---|
| `CF_SPACE_PROD` |
| `CF_DOMAIN_PROD` |
| `DATASPHERE_BASE_URL_PROD` |
| `DATASPHERE_SPACE_ID_PROD` |
| `DATASPHERE_TOKEN_URL_PROD` |
| `DATASPHERE_CLIENT_ID_PROD` |
| `DATASPHERE_CLIENT_SECRET_PROD` |
| `SAC_TENANT_URL_PROD` |
| `SAC_TOKEN_URL_PROD` |
| `SAC_CLIENT_ID_PROD` |
| `SAC_CLIENT_SECRET_PROD` |
| `AICORE_DEPLOYMENT_ID_PROD` |

---

## 3. Add secrets via GitHub CLI (fastest)

```bash
# Install gh CLI: https://cli.github.com
gh auth login

REPO="your-org/bobj-converter"

# Shared
gh secret set CF_API_URL          --repo $REPO --body "https://api.cf.eu20.hana.ondemand.com"
gh secret set CF_USERNAME          --repo $REPO --body "your@email.com"
gh secret set CF_PASSWORD          --repo $REPO --body "yourpassword"
gh secret set CF_ORG               --repo $REPO --body "your-cf-org"

# Dev environment secrets
gh secret set CF_SPACE_DEV         --repo $REPO --env dev --body "dev"
gh secret set CF_DOMAIN_DEV        --repo $REPO --env dev --body "cfapps.eu20.hana.ondemand.com"
gh secret set DATASPHERE_BASE_URL_DEV --repo $REPO --env dev --body "https://..."
# ... repeat for all dev secrets

# Prod environment secrets
gh secret set CF_SPACE_PROD        --repo $REPO --env prod --body "prod"
gh secret set CF_DOMAIN_PROD       --repo $REPO --env prod --body "cfapps.eu20.hana.ondemand.com"
# ... repeat for all prod secrets
```

---

## 4. Branch protection rules

In **Settings → Branches → Add rule** for `main`:

- [x] Require a pull request before merging
- [x] Require status checks to pass: `lint`, `unit-tests`, `integration-tests`
- [x] Require branches to be up to date before merging
- [x] Do not allow bypassing the above settings

---

## 5. Tagging for prod releases

```bash
# Prod deploys trigger automatically on semver tags
git tag v1.2.0
git push origin v1.2.0
# → triggers deploy-prod.yml → waits for reviewer approval → deploys
```

---

## 6. Manual rollback

Go to **Actions → Rollback → Run workflow**, select environment, type `ROLLBACK`.
