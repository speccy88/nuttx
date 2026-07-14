print("P2BERRY:CORE:START")

values = [6, 7]
caught = false

try
    raise "p2_smoke", "expected exception"
except .. as e, message
    if e == "p2_smoke"
        caught = true
    end
end

if values[0] * values[1] != 42 || caught == false
    raise "p2_smoke", "core arithmetic or exception test failed"
end

print("P2BERRY:CORE=PASS:VALUE=42:EXCEPTION=PASS")
