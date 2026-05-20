# Keep the bot awake

Two cheap ways to stop Windows from sleeping the agent_hub process
mid-task. Pick one or use both.

## Option 1 — PowerToys Awake (on-demand override)

Install once:

```powershell
winget install Microsoft.PowerToys
```

(Or download from https://github.com/microsoft/PowerToys/releases.)

After install:

1. Right-click the PowerToys tray icon → **Settings** → enable **Awake**.
2. An Awake tray icon (a coffee cup) appears next to the clock.
3. Click it → choose a mode:
   - **Keep awake indefinitely** — bot runs until you toggle off
   - **Keep awake for a time interval** — e.g. 4 hours
   - **Keep awake until expiration** — pick a specific time
4. Leave "Keep screen on" **off** unless you actually want the display
   backlit (you usually don't).

**Toggle Awake off before you put the laptop in a bag.** A closed bag
with the machine awake will drain the battery and may overheat without
airflow.

## Option 2 — Lid-close power config (automatic on AC)

Settings GUI:

1. Win + R → run `control.exe powercfg.cpl,,3`
   *(or Settings → System → Power → Additional power settings →
   "Choose what closing the lid does")*
2. "When I close the lid":
   - **On battery: Sleep**
   - **Plugged in: Do nothing**
3. Save changes.

PowerShell equivalent (run **as administrator**):

```powershell
# Plugged in: don't sleep when lid closes
powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
# Battery: still sleep (preserve battery)
powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
powercfg /SETACTIVE SCHEME_CURRENT
```

Verify:

```powershell
powercfg /Q SCHEME_CURRENT SUB_BUTTONS LIDACTION
```

You want:

- `Current AC Power Setting Index: 0x00000000` (= "Do nothing")
- `Current DC Power Setting Index: 0x00000001` (= "Sleep")

LIDACTION value reference: `0`=Do nothing, `1`=Sleep, `2`=Hibernate,
`3`=Shut down.

## When to use which

| Scenario                                | Best choice |
| --------------------------------------- | ----------- |
| At desk, plugged in, lid closing        | Option 2 (automatic) |
| At desk, lid open, walking away         | Option 1 (toggle Awake) |
| Battery only, away from outlet          | Don't expect the bot to keep running — Telegram will queue your messages and process them when you return to power |
| Overnight, laptop charging              | Either; option 2 needs no thought |

## Verifying the bot survived a "would-have-slept" period

When you come back to the laptop after a closed-lid session:

- Send `/status` in Telegram. A response means the poll loop is alive.
- Any messages you sent during the closed-lid window should already be
  processed (Telegram queues, then delivers on poll resume).
- If `/status` doesn't respond, the bot likely crashed at some point —
  restart with `python -m agent_hub` and the orchestrator will release
  any stuck handoff claims on boot (see `Orchestrator.start()` →
  `release_stale_claims`).
