from src.verification import get_expected_sha256, get_current_sha256, is_deterministic

def main():
    expected_sha256 = get_expected_sha256()
    current_sha256 = get_current_sha256()
    is_det = is_deterministic()

    report = f"Artifact Hash: {current_sha256}\n"
    report += f"Comparison Status: {'Match' if current_sha256 == expected_sha256 else 'Mismatch'}\n"
    report += f"Determinism Score: {'Pass' if is_det else 'Fail'}\n"
    report += f"Environmental Constraints: OS, SDK, Build Tool Version\n"

    print(report)

if __name__ == '__main__':
    main()
