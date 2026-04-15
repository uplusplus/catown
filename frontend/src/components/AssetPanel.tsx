import { formatRelative, titleize, truncate } from '../lib/format';
import type { Asset } from '../types';

type Props = {
  assets: Asset[];
  selectedAssetId: number | null;
  onSelect: (assetId: number) => void;
};

export function AssetPanel({ assets, selectedAssetId, onSelect }: Props) {
  return (
    <section className="panel-shell asset-panel">
      <div className="section-header">
        <div>
          <p className="eyebrow">Deliverables</p>
          <h3>Assets</h3>
        </div>
        <span className="section-stat">{assets.length} current</span>
      </div>
      <div className="asset-list">
        {assets.map((asset) => (
          <button
            key={asset.id}
            className={`asset-card ${asset.id === selectedAssetId ? 'is-active' : ''}`}
            onClick={() => onSelect(asset.id)}
            type="button"
          >
            <div className="asset-card-top">
              <strong>{asset.title || titleize(asset.asset_type)}</strong>
              <span className={`stage-badge stage-${asset.status}`}>v{asset.version}</span>
            </div>
            <p>{truncate(asset.summary, 105) || 'No asset summary yet.'}</p>
            <div className="asset-card-meta">
              <span>{titleize(asset.asset_type)}</span>
              <span>{formatRelative(asset.updated_at)}</span>
            </div>
          </button>
        ))}
        {assets.length === 0 ? <div className="empty-card">No generated assets yet.</div> : null}
      </div>
    </section>
  );
}
