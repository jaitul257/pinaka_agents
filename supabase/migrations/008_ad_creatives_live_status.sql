-- Phase 6.1 fix: add 'live' status to track creatives that have been flipped
-- from PAUSED → ACTIVE on Meta. Previously we only had 'published' (meaning
-- "pushed to Meta, status=PAUSED"), so after Go Live the dashboard still
-- showed "Paused on Meta" because the DB state never changed.

ALTER TABLE ad_creatives DROP CONSTRAINT IF EXISTS ad_creatives_status_check;

ALTER TABLE ad_creatives ADD CONSTRAINT ad_creatives_status_check
    CHECK (status IN ('pending_review','publishing','published','live','rejected','paused'));
