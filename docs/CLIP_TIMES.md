# Clip times: what a file name says, and why clips are never dropped for starting near another

Measured on 11,082 front clips (7,431 Recent, 2,066 Sentry, 1,585 Saved, from `teslacam_provenance`), and on every file name on the four drives (Archive, Tesla USB, JOWUA, LaCie).

## The file name
`YYYY-MM-DD_HH-MM-SS-front.mp4` is the second the clip began recording, on the car's clock (the local clock, no zone). The mp4 header holds the same instant in UTC (`creation_time`) and the closing time (`modified`), so a clip's real span is [created, modified].
- name minus creation_time is a whole multiple of 15 minutes (the time zone) for 10,015 clips (90.4 %), 1 s out for 900 (8.1 %), and a few seconds or more out for 167 (1.5 %: the car's clock slipped; `clip_clock` corrects those).
- A clip lasts a minute (median 60.0 s for every kind) unless the car closed it early: under 55 s are 0.3 % of Recent, 11.5 % of Sentry, 11.7 % of Saved. A Sentry clip ends when the event ends or when the car wakes and Recent takes over; a Saved clip ends when it was saved.
- The next clip starts where the last one closed: of 11,081 clips in time order, 10,804 begin within 2 s of the end of the one before; 23 after a 2 to 30 s hole; 39 after 30 s to 10 min; 215 after more than 10 min (the car parked).

## Clips do not overlap
- No two clips overlap by more than 2 s (0 of 11,081 neighbours); no two clips share a start instant; no Sentry or Saved clip is a copy of a Recent one.
- Of 4,511 Saved and Sentry clip names on the drives, 1 has the name of a Recent clip (a 0.03 s stub the car wrote as the Recent minute began), 194 start within 59 s of a Recent clip, and 4,316 are 60 s or more from any. Of the 154 near ones whose headers are read, none overlaps: each ends where the Recent clip begins.

So a clip that starts near another is a different stretch of time. The app used to treat a Saved or Sentry minute within 30 s of a Recent one as "a repeat" and drop it, which left holes. It now keeps every clip; the same name (one file on two drives) is the same clip.

Example: a Sentry event is detected at 11:05:20. The Sentry clip `11-05-19` is 13.5 s long (closed at 11:05:32.5) and Recent clip `11-05-33` begins right after. If a clip's length has not been read yet (it is missing from `teslacam_provenance`), it is taken for a whole minute and dropped, leaving the moment of detection black.
