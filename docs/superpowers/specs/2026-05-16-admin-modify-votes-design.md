# Admin Modify Votes — Design Spec
**Date:** 2026-05-16

## Problem
Admins need to correct ranking votes after they've been submitted or after the voting deadline has passed. Currently both scenarios are hard-blocked by the backend.

## Scope
- Only applies to the **last recorded match** (highest `id` where `is_recorded=True`)
- Admin can modify any player's existing vote (not just admin-submitted ones)
- Modifying a vote triggers automatic recomputation of `rank_winners` / `rank_losers`

---

## Backend

### New Endpoint
```
PUT /matches/{mid}/votes/{voter_player_id}
Header: X-Admin-Pin (required)
Body: MatchVoteIn (reuses existing model)
```

**Logic:**
1. Verify admin pin — raise 401 if invalid
2. Verify match exists — raise 404 if not
3. Verify `mid` is the last recorded match — raise 400 if not
4. Verify a vote exists for `voter_player_id` in this match — raise 404 if not
5. Update `rank_1` through `rank_5` on the existing `MatchVote` record
6. Set `voted_by_admin = True`, update `submitted_at` to now
7. Call `_borda_recompute(db, match)` to recalculate `rank_winners` / `rank_losers`
8. Return updated `MatchVotesOut`

**No deadline check. No already_voted check.**

---

## Frontend

### Trigger
Button **"Modificar voto"** appears alongside "Votar por otro" in the last match card of the match history. Hidden for all other matches.

### Modal Flow
1. Dropdown listing all players who **already have a vote** in this match
2. On player selection — load and pre-populate their current `rank_1`–`rank_5` in the existing ranking drag UI
3. Warning banner (yellow): *"⚠️ Esta acción reemplaza el voto del jugador y recalcula el ranking"*
4. **"Guardar"** button — calls `PUT /matches/{mid}/votes/{voter_player_id}`, closes modal, refreshes match data

### State
- Uses `voteData.individual_votes` (already returned by `GET /matches/{mid}/votes`) to populate the dropdown and pre-fill the ranking
- No new API calls needed for initial load — data already fetched when the votes modal opens

---

## Data Flow
```
Admin clicks "Modificar voto"
  → Modal opens with voters dropdown
  → Admin selects player → current rank pre-filled
  → Admin edits ranking → clicks Guardar
  → PUT /matches/{mid}/votes/{voter_player_id}
  → Backend updates vote + recomputes rank_winners/rank_losers
  → Frontend refreshes match card
```

---

## Out of Scope
- Modifying votes on non-last matches (UI restriction only, no backend enforcement needed given admin trust level)
- Adding new votes via this flow (existing "Votar por otro" covers that)
- Audit log of who changed what
