"""Do not let an upstream boot updater replace the cluster_jetson runtime."""

CAPABILITY = 'carrot_boot_update_v1'


def wait_for_boot_update(client, peer, wifi, connected, publish, **kwargs):
  if peer.get('carrot_host') == 'jetson' and peer.get(CAPABILITY) is True:
    raise RuntimeError('Install the cluster_jetson host runtime; upstream automatic boot updates are not supported')
