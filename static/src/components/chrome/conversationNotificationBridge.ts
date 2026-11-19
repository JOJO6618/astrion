export interface ConversationBanner {
  id: string;
  type: 'completed' | 'approval' | 'question';
  title: string;
  workspaceLabel: string;
}

export interface ConversationNotificationsBridge {
  onBanners: (listener: (banners: ConversationBanner[]) => void) => () => void;
  ready: () => void;
  open: (id: string) => void;
  dismiss: (id: string) => void;
}
