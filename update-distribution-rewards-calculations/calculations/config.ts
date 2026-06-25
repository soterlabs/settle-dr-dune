export const config = {
  accessibilityRewards: {
    //pre 2026
    rewardRates: {
      sparkAPY: 0.004,
      nonSparkRewardPercentage: 0.002
    },
    partners: [
      {
        network: 'ethereum',
        partnerName: 'Sky Farm',
        contractAddress: '0x0650CAF159C5A49f711e8169D4336ECB9b950275',
        tokenCode: 'USDS',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Staked',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Withdrawn',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        proxyAbi: [],
        monitoredEvents: ['Staked', 'Withdrawn', 'Referral']
      },
      {
        network: 'ethereum',
        partnerName: 'Spk Farm',
        contractAddress: '0x173e314C7635B45322cd8Cb14f44b312e079F3af',
        tokenCode: 'USDS',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Staked',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Withdrawn',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        proxyAbi: [],
        monitoredEvents: ['Staked', 'Withdrawn', 'Referral']
      },
      {
        network: 'ethereum',
        partnerName: 'sUSDS Farm',
        contractAddress: '0xa3931d71877c0e7a3148cb7eb4463524fec27fbd',
        tokenCode: 'sUSDS',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'ethereum',
        partnerName: 'sUSDC Farm',
        contractAddress: '0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE',
        l2: true, //l2 are used to award spark with a higher rate
        tokenCode: 'sUSDC',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'base',
        partnerName: 'sUSDC Farm',
        tokenCode: 'sUSDC',
        l2: true,
        contractAddress: '0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'arbitrum',
        partnerName: 'sUSDC Farm',
        l2: true,
        contractAddress: '0x940098b108fb7d0a7e374f6eded7760787464609',
        tokenCode: 'sUSDC',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'unichain',
        partnerName: 'sUSDC Farm',
        contractAddress: '0x14d9143becc348920b68d123687045db49a016c6',
        tokenCode: 'sUSDC',
        l2: true,
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'optimism',
        partnerName: 'sUSDC Farm',
        contractAddress: '0xcf9326e24ebffbef22ce1050007a43a3c0b6db55',
        tokenCode: 'sUSDC',
        l2: true,
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'base',
        partnerName: 'sUSDS Farm',
        contractAddress: '0x1601843c5E9bC251A3272907010AFa41Fa18347E',
        tokenAddress: '0x5875eEE11Cf8398102FdAd704C9E96607675467a',
        tokenCode: 'sUSDS',
        // seeds: {
        //     '0x2917956eff0b5eaf030abdb4ef4296df775009ca': 7915789,
        //     '0x6f3066538a648b9cfad0679df0a7e40882a23aa4': 10,
        // },
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'assetIn',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'assetOut',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amountIn',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amountOut',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'referralCode',
                type: 'uint256'
              }
            ],
            name: 'Swap',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          }
        ],
        monitoredEvents: ['Swap', 'Transfer']
      },
      {
        network: 'optimism',
        partnerName: 'sUSDS Farm',
        contractAddress: '0xe0F9978b907853F354d79188A3dEfbD41978af62',
        tokenAddress: '0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0',
        tokenCode: 'sUSDS',
        // seedAddress: '0x876664f0c9Ff24D1aa355Ce9f1680AE1A5bf36fB',
        // seedValue: 94897183.798138041905537932,
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'assetIn',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'assetOut',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amountIn',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amountOut',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'referralCode',
                type: 'uint256'
              }
            ],
            name: 'Swap',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          }
        ],
        monitoredEvents: ['Swap', 'Transfer']
      },
      {
        network: 'arbitrum',
        partnerName: 'sUSDS Farm',
        contractAddress: '0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266',
        tokenAddress: '0xdDb46999F8891663a8F2828d25298f70416d7610',
        tokenCode: 'sUSDS',
        // seedAddress: '0x92afd6F2385a90e44da3a8B60fe36f6cBe1D8709',
        // seedValue: 96165486.74860082,
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'assetIn',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'assetOut',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amountIn',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amountOut',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'referralCode',
                type: 'uint256'
              }
            ],
            name: 'Swap',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          }
        ],
        monitoredEvents: ['Swap', 'Transfer']
      },
      {
        network: 'unichain',
        partnerName: 'sUSDS Farm',
        contractAddress: '0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f',
        tokenAddress: '0xA06b10Db9F390990364A3984C04FaDf1c13691b5',
        tokenCode: 'sUSDS',
        // seedAddress: '0x345E368fcCd62266B3f5F37C9a131FD1c39f5869',
        // seedValue: 94897183.798138041905537933,
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'assetIn',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'assetOut',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amountIn',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amountOut',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'referralCode',
                type: 'uint256'
              }
            ],
            name: 'Swap',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          }
        ],
        monitoredEvents: ['Swap', 'Transfer']
      },
      {
        network: 'ethereum',
        partnerName: 'Chronicle',
        contractAddress: '0x10ab606b067c9c461d8893c47c7512472e19e2ce',
        tokenCode: 'USDS',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Staked',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Withdrawn',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'user',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'amount',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        proxyAbi: [],
        monitoredEvents: ['Staked', 'Withdrawn', 'Referral']
      },
      {
        network: 'ethereum',
        partnerName: 'stUSDS Farm',
        contractAddress: '0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9',
        tokenCode: 'stUSDS',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'ethereum',
        partnerName: 'spUSDC Farm',
        decimals: 6,
        contractAddress: '0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d',
        tokenCode: 'spUSDC',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'ethereum',
        partnerName: 'spUSDT Farm',
        decimals: 6,
        contractAddress: '0xe2e7a17dFf93280dec073C995595155283e3C372',
        tokenCode: 'spUSDT',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'ethereum',
        partnerName: 'spPYUSD Farm',
        decimals: 6,
        contractAddress: '0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354',
        tokenCode: 'spPYUSD',
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      },
      {
        network: 'avalanche',
        partnerName: 'spUSDC Farm',
        contractAddress: '0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d',
        tokenCode: 'spUSDC',
        decimals: 6,
        contractAbi: [
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Deposit',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'sender',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'receiver',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Withdraw',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'address',
                name: 'from',
                type: 'address'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'to',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'value',
                type: 'uint256'
              }
            ],
            name: 'Transfer',
            type: 'event'
          },
          {
            anonymous: false,
            inputs: [
              {
                indexed: true,
                internalType: 'uint16',
                name: 'referral',
                type: 'uint16'
              },
              {
                indexed: true,
                internalType: 'address',
                name: 'owner',
                type: 'address'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'assets',
                type: 'uint256'
              },
              {
                indexed: false,
                internalType: 'uint256',
                name: 'shares',
                type: 'uint256'
              }
            ],
            name: 'Referral',
            type: 'event'
          }
        ],
        monitoredEvents: ['Deposit', 'Withdraw', 'Transfer', 'Referral']
      }
    ]
  },
  tokenPrices: {
    dataProviderURL: `https://api.g.alchemy.com/prices/v1/${process.env.RPC_KEY}/tokens/historical`,
    network: 'eth-mainnet',
    tokenCodes: [
      {
        name: 'USDS',
        tokenAddress: '0xdC035D45d973E3EC169d2276DDab16f1e407384F'
      },
      {
        name: 'sUSDS',
        tokenAddress: '0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD'
      },
      {
        name: 'stUSDS',
        tokenAddress: '0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9'
      },
      {
        name: 'USDT',
        tokenAddress: '0xdac17f958d2ee523a2206206994597c13d831ec7'
      },
      {
        name: 'PYUSD',
        tokenAddress: '0x6c3ea9036406852006290770bedfcaba0e23a0e8'
      },
      {
        name: 'spUSDT',
        tokenAddress: '0xe2e7a17dFf93280dec073C995595155283e3C372'
      },
      {
        name: 'spUSDC',
        tokenAddress: '0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d'
      },
      {
        name: 'spPYUSD',
        tokenAddress: '0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354'
      },
      {
        name: 'pyUSD',
        tokenAddress: '0x6c3ea9036406852006290770bedfcaba0e23a0e8'
      },
      {
        name: 'USDC',
        tokenAddress: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
      },
      {
        name: 'USDG',
        tokenAddress: '0xe343167631d89B6Ffc58B88d6b7fB0228795491D'
      }
    ]
  }
};
