"""Package marker. The program is in `engine.py`.

Why a named module rather than this file: card 1 names modules relative to
the analysis root's PARENT, so the root's own basename is the first dotted
component. Putting the program in `engine.py` makes the element ids
`wire_intents.engine::score` -- stable names an intents file can be written
against -- while leaving `engine` importable by the harness with the analysis
root on `sys.path`. Both halves must agree on the same directory: the run
refuses if the target it is about to execute is not the tree the graph was
built from.
"""
