"""Making and changing the drawing.

The board is the one thing in this system a person authors directly. Everything
else is derived from it: lint reads it, the reviewer asks about it, the freeze
seals it, and a code generator builds from what the freeze produced.

The rule that shapes every module here: **an edit changes exactly the element it
names and nothing else.** No cascade, no tidying up, no helpful inference about
what someone probably meant. After a review round some of these values carry a
human answer behind them, and quietly editing one on a card being edited would
destroy knowledge nobody asked to destroy.

What is refused here is only what cannot be *stored* — a config that would not
load, a duplicate key, a self-edge that is not a repeat. What is merely
unfinished is stored and reported, because a missing value is a question for the
review, never an error handed to somebody who came to draw a diagram.
"""
