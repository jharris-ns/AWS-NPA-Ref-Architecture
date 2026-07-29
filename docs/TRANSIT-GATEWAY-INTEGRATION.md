# Transit Gateway Integration with NPA

Post-deployment guide for connecting the Netskope NPA VPC to an AWS Transit Gateway (TGW).

---

## Overview

AWS Transit Gateway acts as a Regional virtual router for traffic flowing between your VPCs and on-premises networks. It replaces complex full-mesh VPC peering with a hub-and-spoke topology, routing packets to specific next-hop attachments based on destination IP addresses.

For network engineers, the TGW functions as a distributed **Core Router** or **Collapsed Backbone** — all network segments (VPCs) connect to a central hub for routing and policy enforcement.

---

## Key Concepts

Understanding how TGW interacts with VPCs is essential before configuring routing.

### TGW Attachments

A TGW attachment acts as both a source and destination of packets. When attaching a VPC, AWS deploys an elastic network interface (ENI) in the subnets you select.

- You must enable **at least one subnet per Availability Zone** where you want traffic routed.
- Traffic leaving an EC2 instance is sent to this ENI to enter the TGW hub.

### TGW Route Tables (Hub VRFs)

TGW route tables function similarly to **VRF instances** in traditional networking, allowing you to isolate subsets of attachments.

- **Default Route Table** — All attachments are associated and propagated here by default, providing full connectivity.
- **Custom Route Tables** — Used for segmentation (e.g., separating Dev from Prod environments).

### Route Table Association (Ingress Policy)

A TGW attachment is associated with **exactly one** TGW route table. This controls ingress logic: when a packet enters the TGW from a VPC, the router looks up the destination IP only within the route table associated with that VPC's attachment.

### Route Propagation (Route Learning)

Propagation is the mechanism for dynamic route advertisement. When an attachment is propagated to a TGW route table, the CIDR blocks of that attachment are automatically installed in the table.

- Unlike association (1-to-1), an attachment **can be propagated to multiple route tables**, allowing different network segments to learn how to reach a given VPC.

### VPC Route Tables (The On-Ramp)

The TGW does not automatically override local VPC routing. Each VPC subnet must have an explicit static route directing traffic to the TGW:

```
Destination: <remote CIDR or 0.0.0.0/0>
Target:      <tgw-attach-xxxxxxxxxxxxxxxxx>
```

---

## Scenario 1: Simple Connectivity (Centralized Router)

**Objective:** Enable bidirectional communication between the NPA VPC and a remote Production VPC in a flat network.

### Architecture

All VPC attachments are associated with the **Default TGW Route Table** and propagate their routes to it. This creates a single global routing table where all connected networks can reach each other.

![Simple hub-and-spoke TGW topology](images/Transit%20Gateway%20Example%20Simple.png)

```
NPA VPC (10.0.0.0/16) ──┐
                          ├── [Default TGW Route Table] ── Prod VPC (192.168.0.0/16)
                         └──────────────────────────────┘
```

### Configuration Steps

#### Step 1 — Create the TGW Attachment for the NPA VPC

1. In the AWS Console, navigate to **VPC → Transit Gateways → Transit Gateway Attachments**.
2. Click **Create Transit Gateway Attachment**.
3. Select your TGW ID.
4. Set **Attachment type** to `VPC`.
5. Select the **NPA VPC** and choose the **Publisher subnet(s)**.
6. Click **Create**.

Repeat for the Prod VPC.

#### Step 2 — Update VPC Route Tables

Add a static route in each VPC's route table pointing to the TGW attachment:

**NPA VPC Route Table**

| Destination      | Target             |
|------------------|--------------------|
| 192.168.0.0/16   | tgw-attach-*       |

**Prod VPC Route Table**

| Destination  | Target         |
|--------------|----------------|
| 10.0.0.0/16  | tgw-attach-*   |

#### Step 3 — Verify TGW Default Route Table Propagation

1. Navigate to **VPC → Transit Gateways → Transit Gateway Route Tables**.
2. Select the **Default** route table.
3. Confirm both attachments appear under the **Propagations** tab.
4. Confirm routes are visible under the **Routes** tab:

| CIDR             | Type       | Attachment        |
|------------------|------------|-------------------|
| 10.0.0.0/16      | Propagated | NPA VPC attachment|
| 192.168.0.0/16   | Propagated | Prod VPC attachment|

See [Appendix A](#appendix-a-troubleshooting-scenario-1) if connectivity fails.

---

## Scenario 2: Network Segmentation (Isolated Spokes)

**Objective:** Isolate Development (172.16.0.0/16) from Production (192.168.0.0/16) while allowing the NPA VPC (10.0.0.0/16) to communicate with both.

### Architecture

Three custom TGW route tables act as isolated VRFs. Prod and Dev cannot reach each other; the NPA VPC has routes to both.

![Segmented multi-VPC TGW topology — spokes isolated, NPA reachable from all](images/Transit%20Gateway%20Example.png)

```
Prod VPC ──[Prod-TGW-RTB]──┐
                             ├── NPA VPC ──[NPA-TGW-RTB]── (routes to Prod + Dev)
Dev VPC  ──[Dev-TGW-RTB] ──┘
```

### Configuration Steps

#### Step 1 — Create Three Custom TGW Route Tables

1. Navigate to **VPC → Transit Gateways → Transit Gateway Route Tables**.
2. Create three route tables and name them:
   - `Prod-TGW-RTB`
   - `Dev-TGW-RTB`
   - `NPA-TGW-RTB`

#### Step 2 — Configure Associations (Ingress Policy)

Associate each VPC attachment with its dedicated route table. Each attachment can only be associated with **one** table.

| VPC Attachment  | Associate With  |
|-----------------|-----------------|
| Prod VPC        | Prod-TGW-RTB    |
| Dev VPC         | Dev-TGW-RTB     |
| NPA VPC         | NPA-TGW-RTB     |

#### Step 3 — Configure Propagations (Route Learning)

Propagate attachments selectively to control which routes each VRF can see:

| Attachment to Propagate | Propagate Into   | Effect                                      |
|-------------------------|------------------|---------------------------------------------|
| NPA VPC attachment      | Prod-TGW-RTB     | Prod can reach NPA (10.0.0.0/16)            |
| NPA VPC attachment      | Dev-TGW-RTB      | Dev can reach NPA (10.0.0.0/16)             |
| Prod VPC attachment     | NPA-TGW-RTB      | NPA can reach Prod (192.168.0.0/16)         |
| Dev VPC attachment      | NPA-TGW-RTB      | NPA can reach Dev (172.16.0.0/16)          |

> **Do NOT** propagate the Prod attachment into `Dev-TGW-RTB` or the Dev attachment into `Prod-TGW-RTB`. Doing so breaks isolation.

#### Step 4 — Update VPC Route Tables

Add static routes in each VPC pointing to the TGW for all remote CIDRs it needs to reach:

**NPA VPC Route Table**

| Destination      | Target       |
|------------------|--------------|
| 192.168.0.0/16   | tgw-attach-* |
| 172.16.0.0/16   | tgw-attach-* |

**Prod VPC Route Table**

| Destination  | Target       |
|--------------|--------------|
| 10.0.0.0/16  | tgw-attach-* |

**Dev VPC Route Table**

| Destination  | Target       |
|--------------|--------------|
| 10.0.0.0/16  | tgw-attach-* |

See [Appendix B](#appendix-b-troubleshooting-scenario-2) if isolation fails or NPA cannot reach a spoke.

---

## Concept Analogy

Think of the Transit Gateway as a **secure office building**:

- **Scenario 1 (Simple)** is an *open plan* office. Everyone sits in one large room (Default Route Table). Any VPC can reach any other directly.

- **Scenario 2 (Segmented)** is a building with *keycard access*:
  - **Associations** are the doors you enter. Entering the "Production Door" places you in the "Production Corridor" (Prod VRF).
  - **Route Tables** are the directory signs on the wall. In the Production Corridor, the sign points to "NPA Office" but lists no "Development Office" — you physically cannot get there.
  - **NPA (Shared Services) has a Master Key.** It enters a corridor that lists every office, allowing it to visit both Production and Development.

---

## Appendix A: Troubleshooting Scenario 1

If connectivity fails in the Simple Connectivity scenario, verify the following:

| Component              | Check              | Correct Configuration                                  | Reason                                                  |
|------------------------|--------------------|--------------------------------------------------------|---------------------------------------------------------|
| NPA VPC Route Table    | Static Route       | Dest: `192.168.0.0/16` → Target: `tgw-attach-*`       | Traffic must be explicitly routed out of the VPC        |
| Prod VPC Route Table   | Static Route       | Dest: `10.0.0.0/16` → Target: `tgw-attach-*`          | Return traffic must know how to reach the TGW           |
| TGW Default Route Table| Propagated Route   | Dest: `10.0.0.0/16` (NPA) — Type: Propagated          | Ensures TGW knows where the NPA VPC is located          |
| TGW Default Route Table| Propagated Route   | Dest: `192.168.0.0/16` (Prod) — Type: Propagated      | Ensures TGW knows where the Prod VPC is located         |

---

## Appendix B: Troubleshooting Scenario 2

If isolation fails (Prod can ping Dev) or NPA cannot reach a spoke, work through these steps:

**Step 1 — Verify Associations (Ingress)**

Confirm the Prod VPC attachment is associated **only** with `Prod-TGW-RTB`.

- If it is associated with `NPA-TGW-RTB`, it inherits routes to Dev and isolation is immediately broken.

**Step 2 — Verify TGW Route Entries (Egress)**

Inspect `Prod-TGW-RTB`:

- **Correct:** Contains `10.0.0.0/16` (NPA).
- **Incorrect:** Contains `172.16.0.0/16` (Dev).
- **Action:** If the Dev route exists, verify that propagation of the Dev attachment to `Prod-TGW-RTB` is disabled.

**Step 3 — Verify Return Paths (Asymmetric Routing)**

When NPA pings Prod, the request uses `NPA-TGW-RTB` (which knows where Prod is). The reply from Prod uses `Prod-TGW-RTB`.

- **Check:** Does `Prod-TGW-RTB` have a route to `10.0.0.0/16`? If not, return traffic is dropped (blackholed).
- **Fix:** Ensure the NPA VPC attachment is propagated into `Prod-TGW-RTB`.