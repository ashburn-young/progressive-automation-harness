# The AI Harness — One‑Page Overview

*A plain‑English guide for business and semi‑technical readers.*

---

## What is the AI Harness?

An **AI harness** is the safety scaffolding around an AI agent. It lets AI do real
work **while a human stays in control**, and it captures every human approval to
teach the AI over time. Like a climbing or test harness, it holds a task safely
**until that task can stand on its own** — then the support falls away.

Our **Progressive Automation Harness** turns the everyday manual tasks your people
do by hand into reusable capabilities that **gradually learn to run themselves**.

**The loop, in one line:** your approvals are the training data — so supervising
the AI is exactly what makes your supervision unnecessary.

---

## What is a Skill?

A **Skill** is the durable product the harness creates — the "how we do this,"
written down as a reusable, auditable asset (not a throwaway chat).

- It is **owned by the organization**, not locked in one person's head.
- It **matures**: `Cold → Primed → Deterministic → Autonomous`. The more it's
  approved, the more it runs itself — and the **cheaper** it gets (mature steps
  replay with **no AI call at all**).
- It is **governed like an asset**: a Skills Library with quality grades (A–D),
  version history with rollback, and a lifecycle:
  **draft → validated → published → monitored → retired.**

---

## How it works (4 steps)

1. **Describe** a repetitive task in plain language.
2. **Co‑pilot** runs it while you **Approve / Reject / Edit** each step.
3. **Learn** — every approval trains the Skill.
4. **Reuse** — the Skill takes over the steps you keep approving; you're only
   pulled in for judgment calls and anything high‑risk.

---

## The Azure services that power it

The harness is built entirely from managed Microsoft Azure services you already
know how to govern. Each plays one clear role:

| Role | Azure service | What it does for you |
|------|---------------|----------------------|
| **Brain** (reasoning) | Azure AI Foundry (models) | Turns a described task into a plan and drafts each action |
| **Orchestrator** (host) | Azure Container Apps | Runs the harness loop; scales to zero when idle |
| **Tools** (doing the work) | Azure Functions + MCP tool layer | Executes each step against your real systems |
| **Memory** (short‑term + skills) | Azure Cosmos DB | Stores sessions, learned Skills, and every approval (audit trail) |
| **Knowledge** (long‑term) | Azure AI Search | Grounds actions in your own policies and documents |
| **Guardrails** | Azure AI Content Safety | Screens every action before it runs; unsafe actions go to a human |
| **Identity & access** | Microsoft Entra ID + Managed Identity + RBAC | Sign‑in, and no stored passwords — least‑privilege access |
| **Secrets** | Azure Key Vault | Safely holds any credentials a Skill needs |
| **Governance gateway** | Azure API Management | Rate limits, quotas, and usage control on the AI |
| **Network isolation** | VNet + Private Endpoints | Keeps data and model traffic off the public internet |
| **Observability** | Application Insights + Azure Monitor | Traces every step, with provenance and outcome |
| **Supply chain** | Azure Container Registry | Builds and stores the harness image |

---

## Why it matters (the value)

- **Starts where the work already is** — automate the "long tail" of small,
  repetitive tasks that are too minor for traditional projects. Low‑risk,
  bottom‑up adoption.
- **Humans in control by default** — nothing runs unattended until it's earned
  trust; sensitive actions (send, pay, submit) **always** need a person.
- **Captures know‑how** — continuity, onboarding, and compliance value when
  people are out or move on.
- **Cost decays** — the opposite of AI tools that pay full price on every run,
  forever.
- **Enterprise‑ready** — secure sign‑in, private networking, safety screening,
  and a full audit trail, on infrastructure you already trust.

---

## Honest scope

This is a **working reference solution**, not a shrink‑wrapped product. The
learning loop, human oversight, skill lifecycle, quality scoring, and Azure
deployment are all real and demonstrable. Connecting it to a specific
finance/ERP or line‑of‑business system is a **straightforward integration step**,
not a rebuild — that's the natural first project in a customer engagement.

---

*In one sentence: the AI Harness makes your approvals the training data, so
supervising the AI is what makes your supervision unnecessary — and the reusable
Skill is the asset you can buy, sell, and govern.*
