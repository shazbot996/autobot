# GPU inference server host on WSL

Written outside cr2, in Claude Code, by the model claude-opus-5-5, on 2026-11-27, in a session running on the new host GAMEON1 (WSL2).

> This repo is Autobot, a voice assistant. Until now it has only run on an Intel NUC. You are running on a second host I've just added: a Windows PC (Ryzen 7 5800, NVIDIA RTX 2080 Ti 11GB) running Ubuntu 24.04 in WSL2, with mirrored networking enabled. Its role will be the GPU inference server on my LAN: speech-to-text, a local LLM for routing and the core conversational agent, and possibly text-to-speech. The NUC stays the front end (wake word, mic, speaker, face display) and will call this host over the LAN.
>
> Task: update the repo's spec so it describes this host, so that an agent on the NUC can later build the server code and the networking between the two.
>
> 1. Read the existing spec first, especially docs/infrastructure.md (or wherever infrastructure is described), AGENTS.md, and the cr2-conformance skill in .agents/skills. Follow its conventions: file structure, metadata, rule IDs with a unique prefix and a Reason on each rule, and provenance.
> 2. Inspect this host and record facts, not guesses: Windows and WSL versions (wsl.exe --version), distro, CPU cores, RAM, disk free, GPU model, VRAM, driver and CUDA version (nvidia-smi), Python version, LAN IP and hostname as seen from WSL, and whether mirrored networking is active. Note anything you could not verify.
> 3. Update infrastructure.md to describe both hosts, their roles, and how they connect. Include the LAN addressing approach (static IP or DHCP reservation), the ports the server will expose, the Windows Firewall rule needed, how the services start on boot, and what happens when this host is off or asleep.
> 4. If the spec structure calls for it, add or outline a spec for the server app (a new server/ folder in this monorepo): its API surface for STT, routing and chat, model choices that fit in 11GB VRAM, and constraints. Mark open decisions clearly instead of deciding them.
> 5. Don't write any server code yet, and don't change Windows settings. If a step needs admin rights in Windows, list the command for me instead.
>
> When done, summarize what you changed and the open questions, then commit on the current branch and push so the NUC session can pull it.
