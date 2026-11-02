# Presets are a user choice plus ranked layers, not save and restore

The proxy keeps one user choice (the preset a person last picked, Manual with its temperature) and derives the active preset from it and the layers that hold the room, in a fixed rank: open window over nobody home over boost. No layer ever writes into the user choice, so nothing has to be saved and restored when a layer starts or ends. A change by a person always goes to the user choice; picking the preset an active layer already forces changes nothing. A boost only counts down while it is what heats the room – an open window, nobody home or heating that is off pause it. Turning the heating off leaves the window and presence layers alone; they keep following their sensors.

## Considered Options

- **Save and restore** (up to v1.4.1): each automation overwrote the active preset and kept the previous one in its own saved state. Because the shown preset and the user's choice were one field, it needed a rule for every combination – never save Frost Protection or Away, a restored boost falls back to Comfort, Comfort re-reads its target, the window and presence saved states rewrite each other, turning the heating off ends the window. Each interaction between automations was a new bug (v1.4.0 review, #117, #118).

## Consequences

- The user choice is persisted on its own, so a restart in Away or during a boost brings back the user choice instead of Comfort.
- A boost picked while a window is open or nobody is home starts paused and runs its full time once the room is free. A boost picked while nobody is home therefore waits until someone comes home; pre-heating goes through guest mode.
- A boost paused by heating that is off still shows as the active preset, like a window or nobody home: the preset shows what holds the room once the heating is back on.
- The attributes `window_restore_preset` / `window_restore_target_c` are gone; the first start after the update reads them once.
- The manual timer is no layer: it holds nothing and, when it ends, writes the return preset into the user choice without that counting as a person's tap. A boost and confirming a forced preset leave it running, because they do not touch the user choice: ending it there would leave Manual without an end. While it runs, a preset command without a person only changes the return preset, so Manual runs its full time and a schedule's latest preset applies afterwards. A command counts as coming from a person when the call carries a `user_id`; `parent_id` cannot tell them apart, since a time-triggered automation has none. Token tools (Node-RED, AppDaemon) therefore count as a person, and a dashboard button that goes through an automation does not.
