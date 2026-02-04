After taking a 13 year hiatus from KDE development, [Harald Sitter's talk on KDE Linux](https://tube.kockatoo.org/w/exAQYJWrXoQSU1EDXidjr3) at Akademy 2024 was the perfect storm of nostalgia and inspiration to suck me back in. I've been contributing on and off since then.

This blog post outlines some gaping holes I see in its extensibility model, and how I plan to address them (assuming no objections from other developers).

## The Problem

KDE Linux is being built as an immutable OS without a traditional package manager. The strategy leans heavily on Flatpak for GUI applications, which is fine for your average desktop user. But here's the thing: the Linux community has a relatively large population of CLI fanatics—developers who live in the terminal, who need `$OBSCURE_TOOL` for their workflow, who won't be satisfied with just what comes in a Flatpak.

The OS ships with a curated set of developer tools that we KDE developers decided to include. Want something else? There's a wiki page with suggestions for installation mechanisms we don't officially support—mechanisms that, let's be real, most of us don't even use ourselves. This sets us up for the same reputation trap that caught KDE Neon: just like Neon became known as "for testing KDE software," KDE Linux risks becoming "for developing KDE software only."

There's also a deeper inconsistency here. One of the stated goals is making the end user's system exactly the same as our development systems. But if the tools we actually use day-to-day are already baked into the base image—and thus not part of the extensibility model we're asking users to adopt—then we're not eating our own dog food. We're shipping an experience we don't fully use ourselves.