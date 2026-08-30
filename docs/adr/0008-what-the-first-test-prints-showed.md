# 8. What the first test prints showed

Date: 2026-08-30
Status: Accepted

## Context

Everything before this was verified by slicing. Two test prints of a tall stack
have now run, one with each film material, and both were ruined. Neither
produced a stack that separates. This records what they showed, because the
results contradict things the repository asserted as settled.

**Print 1, PETG film.** The film did not stick well enough. It began peeling at
the first interface layer and the print was lost there. One variable was not
controlled: the plate filament had changed from PLA-CF to PLA Matte for this
print, so the failure is not cleanly attributable to PETG. It needs repeating
before PETG is written off.

**Print 2, Bambu Support W film.** The opposite failure. The film stuck *too*
well, held through base 7, and then the print turned to spaghetti there and was
lost. Adhesion is not the only thing that went wrong at that height, but nothing
below base 7 had released.

**Separation, on what came off print 2.** Two film layers do not buy easy
separation with Support W. The bases need active prying apart, and after they
separate both faces are covered in support shreds that have to be cleaned off.

**Merged pillars.** Bridging the film's islands into one sheet
([ADR 0007](0007-bridge-the-film-so-it-lifts-as-one-sheet.md)) is not
material-independent. With PETG it is right: the sheet lifts and takes the
pillar tops with it. With Support W it is wrong: the merged columns cannot be
peeled off the plate at all and have to be cut out.

## Decision

**Stop testing on full-size stacks.** A nine-plate stack is hours of printing to
answer one question, and both prints so far died partway through and answered
nothing about separation. Testing moves to small, fast-printing plates, and a
full stack is printed only once the small ones come apart.

**Film adhesion is a two-sided constraint with a working window, not a property
that either holds or does not.** Too little and the film peels mid-print and
takes the job with it; too much and the stack will not come apart afterwards.
Neither material has been shown to sit inside that window. Anything in this
repository that describes non-bonding as "the entire mechanism" is overstated
and has been corrected.

**ADR 0007's bridging is reopened, not reversed.** It stands as measured -- it
does what it claims, and for PETG it is still right. What is no longer true is
that it is unconditionally right, so whether the film is bridged should follow
the film material rather than being a fixed default. No change is made here
because there is no measurement yet to choose the new default from.

## Consequences

The test loop gets fast enough to iterate on: adhesion, film thickness, and
bridging can be swept in a print each rather than an evening each.

Nothing in the tool changes yet, so the current defaults still produce a stack
built on an assumption that has now failed twice in opposite directions. The
generator's printing notes no longer promise that the film lifts out in one
piece, because on the one stack that reached separation it did not.

The PETG result stays ambiguous until it is reprinted against the same plate
filament as print 2. Until then "PETG does not stick well enough" is one
candidate explanation and "PLA Matte is a worse substrate than PLA-CF" is
another, and the two prints do not distinguish them.
