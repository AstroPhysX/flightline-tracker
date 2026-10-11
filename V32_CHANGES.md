# Flightline Tracker v32

- Fixed History 500 after browser schedule sync by normalizing naive/aware UTC datetimes before sorting.
- Edge extension updated to 4.1.0. One-click text capture now more reliably clears the Select-All highlight after copy using Escape, ArrowRight, and a harmless top-left deselection click.
- No database migration is required. Existing mixed datetime rows are handled safely when History renders.
