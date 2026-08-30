# Hardware — cableado y notas

## Topología
```
[Botón ESP32-C3 + 18650]
        │ ESP-NOW
        ▼
[Raspberry Pi 5] ── HDMI ──> TV (vertical, 90° horario)
        │  └── USB ──> DAC USB ──> Edifier R12U
        │
        └── WiFi (router dedicado, sin internet)
              ├──> ESP32 + WLED ──> tira WS2812B
              └──> ESP32-C3 ──> MOSFET ──> Deepcool XF120 (PWM 25 kHz)
```

## Reglas
- Cero 220V dentro del baño. Todo baja tensión.
- El parlante se alimenta por USB desde la Pi.
- El router no tiene salida a internet: solo sirve a los nodos.
- El botón usa ESP-NOW, no WiFi: dispara casi instantáneo y no depende del router.

## Pendientes de armado
- [ ] Confirmar kit Pi 5 8GB en Mercado Libre UY
- [ ] Flashear WLED en el ESP32
- [ ] Firmware del nodo ventilador (PWM 25 kHz por MOSFET)
- [ ] Firmware del botón + desoldar LED de actividad
- [ ] Scheduler de cues en Python sobre la Pi
