"""software - Tesla software versions: what each release holds, for which countries and models, and how many cars are on it.

    teslascope.py   the source: Teslascope's public software API (no token), stored as it came
    notes.py        a release note's identity (what it is called) and its text hash (what it says)
    versions.py     version strings: order, line
    settings.py     region groups and labels (config.json "software" block, with defaults)
    index.py        the local SQLite index built from the stored files, and the questions asked of it
    sync.py         fetch what is missing or still moving, then report (also the command line)
"""
