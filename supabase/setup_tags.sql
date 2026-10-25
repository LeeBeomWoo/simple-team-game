alter table public.participants
  add column if not exists special_tag text;

alter table public.participants
  drop constraint if exists participants_special_tag_check;

alter table public.participants
  add constraint participants_special_tag_check
  check (special_tag is null or special_tag in ('black_knight', 'black_rose_ticket'));

alter table public.participants
  add column if not exists black_knight_count integer not null default 0,
  add column if not exists black_rose_ticket_count integer not null default 0,
  add column if not exists drink_pass_count integer not null default 0;

alter table public.rooms
  add column if not exists drink_pass_enabled boolean not null default false;

update public.participants
set black_knight_count = 1
where special_tag = 'black_knight' and black_knight_count = 0;

update public.participants
set black_rose_ticket_count = 1
where special_tag = 'black_rose_ticket' and black_rose_ticket_count = 0;

update public.participants
set special_tag = null
where special_tag is not null;

update public.participants as participant
set drink_pass_count = 1
from public.rooms as room
where participant.room_id = room.id
  and room.drink_pass_enabled = true
  and participant.drink_pass_count = 0;

update public.rooms
set drink_pass_enabled = false
where drink_pass_enabled = true;

alter table public.participants
  drop constraint if exists participants_black_knight_count_check,
  drop constraint if exists participants_black_rose_ticket_count_check,
  drop constraint if exists participants_drink_pass_count_check;

alter table public.participants
  add constraint participants_black_knight_count_check
    check (black_knight_count between 0 and 999),
  add constraint participants_black_rose_ticket_count_check
    check (black_rose_ticket_count between 0 and 999),
  add constraint participants_drink_pass_count_check
    check (drink_pass_count between 0 and 999);

