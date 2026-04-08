-- Add variant_options column to products table for storing Metal/Size variant config
ALTER TABLE products ADD COLUMN IF NOT EXISTS variant_options JSONB DEFAULT '{}'::JSONB;
