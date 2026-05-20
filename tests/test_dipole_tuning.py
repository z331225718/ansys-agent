from aedt_agent.demo.tuning import find_s11_resonance, next_dipole_arm_length, run_fake_dipole_tuning


def test_find_s11_resonance_uses_lowest_s11_db_sample():
    samples = [
        {"frequency": 2.2, "frequency_hz": 2.2e9, "s11_db": -7.0},
        {"frequency": 2.5, "frequency_hz": 2.5e9, "s11_db": -21.0},
        {"frequency": 2.8, "frequency_hz": 2.8e9, "s11_db": -11.0},
    ]

    resonance = find_s11_resonance(samples)

    assert resonance["frequency"] == 2.5
    assert resonance["frequency_hz"] == 2.5e9
    assert resonance["s11_db"] == -21.0


def test_next_dipole_arm_length_shortens_when_resonance_is_low():
    next_length = next_dipole_arm_length(
        current_length_mm=30.0,
        resonance_frequency_hz=2.3e9,
        target_frequency_hz=2.5e9,
    )

    assert round(next_length, 3) == 27.6


def test_fake_dipole_tuning_converges_to_target_frequency():
    result = run_fake_dipole_tuning(
        target_frequency="2.5GHz",
        initial_arm_length_mm=31.0,
        sweep_start="1GHz",
        sweep_stop="4GHz",
        max_rounds=3,
    )

    assert result["status"] == "converged"
    assert result["target_frequency_hz"] == 2.5e9
    assert 1 <= len(result["rounds"]) <= 3
    assert result["rounds"][0]["arm_length_mm"] == 31.0
    final_round = result["rounds"][-1]
    assert abs(final_round["resonance_frequency_hz"] - 2.5e9) / 2.5e9 <= 0.02
    assert "缩短" in result["rounds"][0]["agent_message"]


def test_fake_dipole_tuning_can_use_external_advisor_for_next_length():
    calls = []

    def advisor(context):
        calls.append(context)
        return {
            "next_arm_length_mm": 28.48,
            "message": "LLM 判断谐振偏低，建议缩短到 28.48 mm。",
        }

    result = run_fake_dipole_tuning(
        target_frequency="2.5GHz",
        initial_arm_length_mm=31.0,
        sweep_start="1GHz",
        sweep_stop="4GHz",
        max_rounds=3,
        advisor=advisor,
    )

    assert calls
    assert calls[0]["controlled_variable"] == "dipole_arm_length_mm"
    assert result["rounds"][0]["next_arm_length_mm"] == 28.48
    assert result["rounds"][0]["agent_message"].startswith("LLM 判断")
