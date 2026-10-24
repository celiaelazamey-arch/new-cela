-- Run in the Supabase SQL editor for the project dedicated to this app.
-- Never use the service_role key in Streamlit or browser-facing configuration.
create table if not exists public.film_archive (
  user_id uuid not null references auth.users(id) on delete cascade,
  film_id text not null check (length(film_id) between 1 and 80),
  design_name text not null check (length(design_name) between 1 and 160),
  client_name text not null default '',
  garment_type text not null default 'تيشيرت',
  fabric_shade text not null check (fabric_shade in ('light', 'dark')),
  colors_count integer not null check (colors_count between 1 and 30),
  color_layers jsonb not null default '[]'::jsonb,
  mesh_counts_t jsonb not null default '[]'::jsonb,
  storage_location text not null default '',
  width_cm double precision not null check (width_cm > 0 and width_cm <= 200),
  height_cm double precision not null check (height_cm > 0 and height_cm <= 200),
  notes text not null default '',
  updated_at timestamptz not null default now(),
  primary key (user_id, film_id)
);

alter table public.film_archive enable row level security;
alter table public.film_archive force row level security;
revoke all on public.film_archive from anon;
grant select, insert, update, delete on public.film_archive to authenticated;

drop policy if exists "users read own film archive" on public.film_archive;
create policy "users read own film archive" on public.film_archive
  for select to authenticated using (auth.uid() = user_id);
drop policy if exists "users insert own film archive" on public.film_archive;
create policy "users insert own film archive" on public.film_archive
  for insert to authenticated with check (auth.uid() = user_id);
drop policy if exists "users update own film archive" on public.film_archive;
create policy "users update own film archive" on public.film_archive
  for update to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
drop policy if exists "users delete own film archive" on public.film_archive;
create policy "users delete own film archive" on public.film_archive
  for delete to authenticated using (auth.uid() = user_id);

drop trigger if exists film_archive_updated_at on public.film_archive;
create or replace function public.set_film_archive_updated_at()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end;
$$;
create trigger film_archive_updated_at before update on public.film_archive
  for each row execute function public.set_film_archive_updated_at();
