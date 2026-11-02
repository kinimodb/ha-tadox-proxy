# Tado X Proxy

A Home Assistant thermostat that stands in for a Tado X radiator valve. It regulates the room on an external room sensor and lets window, presence and boost automations hold the room for a while.

## Language

### Presets

**Active preset**:
The preset the room runs on right now. It follows from the active layers and the user choice; nobody sets it directly.
_Avoid_: current preset, effective preset

**User choice**:
The preset a person last picked for the room – in the app or on the valve, and for Manual together with its temperature. It comes back as soon as no layer holds the room. A command without a person (a schedule, an automation) counts the same, except while the manual timer runs: then it only changes the return preset. The end of the manual timer is the only thing that changes the user choice by itself.
_Avoid_: saved state, previous preset, restore preset, base preset

**Manual**:
The preset without a name that a person gets by setting a temperature directly. The only preset that carries its own temperature.
_Avoid_: none preset

**Manual timer**:
An option that ends Manual after a set time. A person setting a new temperature starts it again; a person picking another preset ends it. It holds nothing, so it is no layer: it runs on while a layer holds the room.
_Avoid_: manual timeout, timed return

**Return preset**:
The preset that becomes the user choice when the manual timer ends: the one picked before Manual, or the one a command without a person has set since.
_Avoid_: previous preset, restore preset

### Layers

**Layer**:
An automation that holds the room in its own preset over the user choice for a while: an open window holds it in Frost Protection, nobody home in Away, a boost in Boost. An open window outranks nobody home, which outranks a boost.
_Avoid_: overlay, override, automation state

**Boost**:
The layer a person starts for a short burst of heat. Its time only runs while the boost is what heats the room – an open window, nobody home or heating that is off pause it.
_Avoid_: boost mode, boost preset

**Close delay**:
The wait after a window closes before the window's layer lets go of the room.
_Avoid_: restore delay

**Window action**:
What an open window does to the valve: send the Frost Protection target, or switch the valve off. Either way the active preset is Frost Protection.
_Avoid_: window mode

**Guest mode**:
A switch that keeps nobody home from holding the room, so guests get normal heating while the presence sensor reports the house empty.
_Avoid_: party mode, presence override

### Tado

**Overlay**:
Tado's own manual override on the valve. Some cloud integrations let it expire, so the proxy resends it.
_Avoid_: using it for layers
