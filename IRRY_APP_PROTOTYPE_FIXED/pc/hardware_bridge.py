
try:
    import config
    HARDWARE_ENABLED = getattr(config, "HARDWARE_ENABLED", False)
    GPIO_PUMP_PIN = getattr(config, "GPIO_PUMP_PIN", 17)
    GPIO_VALVE_PINS = getattr(config, "GPIO_VALVE_PINS", [22,23,24])
except Exception:
    HARDWARE_ENABLED=False
    GPIO_PUMP_PIN=17
    GPIO_VALVE_PINS=[22,23,24]

try:
    from gpiozero import LED
    GPIO_AVAILABLE=True
except Exception:
    GPIO_AVAILABLE=False

class HardwareDriver:
    def __init__(self):
        self.pump = None
        self.valves = []
        if HARDWARE_ENABLED and GPIO_AVAILABLE:
            self.pump=LED(GPIO_PUMP_PIN)
            self.valves=[LED(p) for p in GPIO_VALVE_PINS]
            print("Hardware: GPIO mode")
        else:
            print("Hardware: simulation mode")

    def set_pump(self,state):
        if self.pump:
            self.pump.on() if state else self.pump.off()
        print(f"Pump {'ON' if state else 'OFF'}")

    def set_valve(self,bed_index,state):
        if 0 <= bed_index < len(self.valves):
            self.valves[bed_index].on() if state else self.valves[bed_index].off()
        print(f"Valve {bed_index+1} {'ON' if state else 'OFF'}")

    def cleanup(self):
        if self.pump: self.pump.off()
        for v in self.valves: v.off()
