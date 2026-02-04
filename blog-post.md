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

**distrobox/toolbox** are built on docker/podman and work great for ephemeral containers—spin one up, do some work, tear it down. But they're not ideal for long-term containers that accumulate lots of state over time, like when you're doing major OS upgrades or maintaining persistent development environments. They'll do it, but it feels like you're fighting against the design.

**systemd-nspawn** takes the opposite approach. It's designed for persistent, long-lived containers from the ground up—has a proper init system, manages services, the whole nine yards. But the permissions model is restrictive by design. Want to run docker or podman inside your nspawn container? That's going to take some work. Need to expose arbitrary hardware devices? You're in for manual configuration. It's secure, which is great, but sometimes you need more flexibility than safety.

**devcontainers** are Microsoft's answer to development environments, and they honestly work great for what they do. The tooling is solid, the integration is smooth. But they haven't caught on in the Linux community—maybe because Microsoft invented them (the irony isn't lost on me, given how many Linux folks use VS Code), or maybe because they require VS Code or a compatible editor. Whatever the reason, they're not getting the adoption they probably deserve in Linux circles.