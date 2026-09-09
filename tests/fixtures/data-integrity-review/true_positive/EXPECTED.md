Expected finding:

[HIGH] Migrations/20260909_ShrinkEmail.cs:4 - Email column narrowed from varchar(255) to varchar(50) with no backfill/validation
Impact: any existing email longer than 50 characters is silently truncated on migration, corrupting user contact data with no error raised.
Fix: add a pre-migration check/backfill that rejects or remediates rows exceeding the new length before narrowing the column, or keep the wider type.
