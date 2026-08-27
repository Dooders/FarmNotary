from farm_notary.keccak import function_selector, keccak256


def test_empty_input():
    assert (
        keccak256(b"").hex()
        == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


def test_abc():
    assert (
        keccak256(b"abc").hex()
        == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    )


def test_multi_block_input():
    # Cross-checked against pycryptodome's keccak (1000 bytes spans 8 blocks).
    assert (
        keccak256(b"x" * 1000).hex()
        == "fa0c9183d89d2dfac84b8da9a1e6a3b1835482f27fd1f4842ad312cc25385d28"
    )


def test_erc20_transfer_selector():
    assert function_selector("transfer(address,uint256)").hex() == "a9059cbb"


def test_registry_selectors():
    assert function_selector("register(bytes32,string)").hex() == "cf2d31fb"
    assert function_selector("records(bytes32)").hex() == "01e64725"
