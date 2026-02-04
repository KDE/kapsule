After taking a 13 year hiatus from KDE development, [Harald Sitter's talk on KDE Linux](https://tube.kockatoo.org/w/exAQYJWrXoQSU1EDXidjr3) at Akademy 2024 was the perfect storm of nostalgia and inspiration to suck me back in. I've been contributing on and off since then.

This blog post outlines some gaping holes I see in its extensibility model, and how I plan to address them (assuming no objections from other developers).

![banana](banana.jpg)

## The Problem

KDE Linux is being built as an immutable OS without a traditional package manager. The strategy leans heavily on Flatpak for GUI applications, which (though, not without its problems) generally works well for its stated goal. But here's the thing: the Linux community has a relatively large population of CLI fanatics—developers who live in the terminal, who need `$OBSCURE_TOOL` for their workflow, who won't be satisfied with just what comes in a Flatpak.

The OS ships with a curated set of developer tools that we KDE developers decided to include. Want something else? There's a wiki page with suggestions for installation mechanisms we don't officially support—mechanisms that, let's be real, most of us don't even use ourselves.

This sets us up for the same reputation trap that caught KDE Neon:

> Just like KDE Neon got pigeonholed with the reputation of being "for testing KDE software," KDE Linux risks getting branded as "for developing KDE software only."

There's also a deeper inconsistency here. One of the stated goals is making the end user's system exactly the same as our development systems. But if the tools we actually use day-to-day are already baked into the base image—and thus not part of the extensibility model we're asking users to adopt—then we're not eating our own dog food. We're shipping an experience we don't fully use ourselves.

## The Solution

![whale](whale.jpg)

Look at the wild success of Docker and Kubernetes. Their container-based approach proved that immutable infrastructure actually works at scale. That success paved the way for Flatpak and Snap to become the de facto solution for GUI apps, and now we're seeing immutable base systems everywhere. The lesson is clear: containers aren't just one solution among many—they're the foundation that makes immutable systems viable.


### Containers for CLI Tools???

As crazy as it sounds, that's the logical next step. Let's look at the candidates to base our solution on top of:

**distrobox/toolbox** are trying to solve the right problem—building long-term, persistent development environments—but they're doing it on top of docker/podman, which were designed for ephemeral containers. They're essentially fighting against the grain of their underlying systems. Every time you want to do something that assumes persistence and state, you're working around design decisions made for a different use case. It works, but you can feel the friction.

**systemd-nspawn** is built for persistence from the ground up, which is exactly what we want. It has a proper init system, it's designed to be long-lived. The challenge here is that we need fine-grained control over the permissions model—specifically, we need to enable things like nested containers (running docker/podman inside the container) and exposing arbitrary hardware devices without a lot of manual configuration. systemd-nspawn makes these scenarios difficult by design, which is great for security but limiting for a flexible development environment.

**devcontainers** nail the developer experience—they're polished, well-integrated, and they just work. The limitation is that they're designed to be used from an IDE like VS Code, not as a system-wide solution. We need something that integrates with the OS itself, not just with your editor. That said, there's definitely lessons to learn from how well they've executed on the developer workflow.

### Our knight in shining armor:

![Inucs](incus-hero3.png)

Enter **Incus**. It checks all the boxes:

- **Proper API** for building tooling on top of it
- **Nested containers work out of the box**—want to run docker inside your Incus container? Go for it
- **Privileged container mode** for when you need full system access and hardware devices
- **Built on LXC**, which means it's designed for long-lived, system-level containers from day one, not retrofitted from ephemeral infrastructure

Bonus: it supports VMs too, for running less trusted workloads. People on Matrix said they want this option. I don't fully get the use case for a development environment, but the flexibility is there if we need it.

