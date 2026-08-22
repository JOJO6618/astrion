// @ts-nocheck
import { useEasterEgg } from '../../composables/useEasterEgg';

export const monitorMethods = {
  async handleEasterEggPayload(payload) {
    const controller = useEasterEgg();
    await controller.handlePayload(payload, this);
  },

  async startEasterEggEffect(effectName, payload = {}) {
    const controller = useEasterEgg();
    await controller.startEffect(effectName, payload, this);
  },

  destroyEasterEggEffect(forceImmediate = false) {
    const controller = useEasterEgg();
    return controller.destroyEffect(forceImmediate);
  },

  finishEasterEggCleanup() {
    const controller = useEasterEgg();
    controller.finishCleanup();
  }
};
