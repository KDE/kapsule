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

As crazy as it sounds, that's the logical next step. Let's look look at the candidates to base our solution on top of:

- distrobox/toolbox
  - based on docker/podman
  - designed for ephemeral containers
  - not good for long term containers with lots of mutations (updating major os releases)

- systemd-nspawn
  - designed for long term containers
  - persistent by default, has init system
  - permissions model too restrictive
    - can't easily run docker/podman inside nspawn
    - can't easily expose all host resources (like hardware devices)

- devcontainers
  - works great for development
  - not very popular in the Linux community
    - possibly because it was invented by Microsoft?
    - requires VSCode or compatible editor