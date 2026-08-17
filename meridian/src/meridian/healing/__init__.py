"""Build → better build.

After the freeze, ground truth lives in the eval suite rather than in a person's
head, so this package's oracle is a test suite where the reviewer's was a human.
That is the only difference between the two loops, and it is the whole reason
this one can run without asking anybody anything.

What lives here is the platform's half: run the cases, compare them, store what
happened, and assemble a failure into something a person can paste. **The repair
itself is a skill somebody runs in a terminal**, which is why there is no
`propose` module and no model call in this package. Legibility is the product;
automation is not.

    sweep     load an agent's entry point, run every case, compare per COLUMN
    compare   the diff, and the tell that a green sweep measured nothing
    localize  a disagreeing column → the primitive that fills it → the file
    bundle    those rows back out as the block §21 describes
    gate      target passes AND nothing that passed before now fails
"""
