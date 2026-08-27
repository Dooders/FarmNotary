// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @notice Stores manifest hash + content CID. Does not execute simulations.
contract SimulationRegistry {
    event Registered(
        address indexed sender,
        bytes32 indexed manifestHash,
        string cid,
        uint256 timestamp
    );

    struct Record {
        address sender;
        string cid;
        uint256 timestamp;
    }

    mapping(bytes32 => Record) public records;

    function register(bytes32 manifestHash, string calldata cid) external {
        require(manifestHash != bytes32(0), "empty hash");
        records[manifestHash] = Record(msg.sender, cid, block.timestamp);
        emit Registered(msg.sender, manifestHash, cid, block.timestamp);
    }
}
