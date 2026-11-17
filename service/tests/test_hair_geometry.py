import cv2
import numpy as np
import pytest

from voxy_service.hair import subdivide as H


def curtain(h=120, w=200, x0=30, x1=170, y0=10, y1=110):
    m = np.zeros((h, w), bool)
    m[y0:y1, x0:x1] = True
    return m


def vertical_flow(h, w):
    f = np.zeros((h, w, 2), np.float32)
    f[..., 1] = 1
    return f


def top_roots(mask, step=5):
    ys, xs = np.where(mask)
    return np.array([[x, ys.min()] for x in range(xs.min() + 2, xs.max(), step)], np.float32)


@pytest.mark.parametrize("count", [2, 4, 7])
def test_subdivisions_partition_the_hair_mask(count):
    m = curtain()
    lab, pivot = H.split_hair(m, vertical_flow(*m.shape), top_roots(m), count, 0.0)
    assert lab.shape == m.shape and lab.dtype == np.int32
    assert ((lab > 0) == m).all()                                # every hair pixel in exactly one subdivision, none outside
    ids = sorted(int(v) for v in np.unique(lab) if v)
    assert ids == list(range(1, len(ids) + 1)) and len(ids) == count
    assert (H.combine_labels(lab) == m).all()                    # Combine gives back the whole Hair mask


def test_vertical_flow_gives_vertical_bands_in_left_to_right_order():
    m = curtain()
    lab, _ = H.split_hair(m, vertical_flow(*m.shape), top_roots(m), 4, 0.0)
    cx = [np.where(lab == k)[1].mean() for k in range(1, 5)]
    assert cx == sorted(cx)                                      # numbered along the root line
    for k in range(1, 5):
        xs = np.where(lab == k)[1]
        assert xs.max() - xs.min() <= 140 / 4 + 6                # a band, not scattered
    areas = [(lab == k).sum() for k in range(1, 5)]
    assert max(areas) / min(areas) < 1.4


def test_subdivisions_follow_the_flow_direction():
    """A 45 degree flow must give bands whose boundaries run at 45 degrees, not vertical ones."""
    h, w = 200, 200
    m = np.zeros((h, w), bool)
    m[20:180, 20:180] = True
    f = np.zeros((h, w, 2), np.float32)
    f[..., 0] = f[..., 1] = np.sqrt(0.5)
    roots = np.array([[20 + i * 8, 20] for i in range(10)] + [[20, 20 + i * 8] for i in range(1, 10)], np.float32)
    lab, _ = H.split_hair(m, f, roots, 3, 0.0)
    # moving along the flow stays in the same subdivision
    inside = lab[40:120, 40:120]
    stay = (inside[:-1, :-1] == lab[41:121, 41:121][:-1, :-1])
    assert stay.mean() > 0.9
    # moving across the flow changes subdivision much more often
    across = (lab[60:140, 60:140][:-1, 1:] == lab[60:140, 60:140][1:, :-1])
    assert across.mean() < stay.mean()


def test_shared_root_pivot_is_the_mean_of_the_roots():
    m = curtain()
    roots = top_roots(m)
    _, pivot = H.split_hair(m, vertical_flow(*m.shape), roots, 3, 0.0)
    assert pivot == pytest.approx(roots.mean(axis=0), abs=1e-3)


def test_count_is_limited_by_the_number_of_roots():
    m = curtain()
    roots = np.array([[60, 10], [100, 10], [140, 10]], np.float32)
    lab, _ = H.split_hair(m, vertical_flow(*m.shape), roots, 10, 0.0)
    assert int(lab.max()) <= 3 and ((lab > 0) == m).all()


def test_no_roots_falls_back_to_the_top_edge():
    m = curtain()
    lab, pivot = H.split_hair(m, vertical_flow(*m.shape), np.zeros((0, 2), np.float32), 3, 0.0)
    assert int(lab.max()) == 3 and ((lab > 0) == m).all()
    assert pivot[1] == pytest.approx(10, abs=1)


def test_smoothing_gives_calmer_boundaries():
    rng = np.random.default_rng(0)
    m = curtain(160, 240, 20, 220, 10, 150)
    ang = cv2.GaussianBlur(rng.normal(0, 1.2, m.shape).astype(np.float32), (0, 0), 3)    # wobbly flow
    f = np.stack([np.sin(ang), np.cos(ang)], axis=2).astype(np.float32)
    roots = top_roots(m, 4)

    def boundary_len(lab):
        e = (lab[:, 1:] != lab[:, :-1]) & (lab[:, 1:] > 0) & (lab[:, :-1] > 0)
        e2 = (lab[1:, :] != lab[:-1, :]) & (lab[1:, :] > 0) & (lab[:-1, :] > 0)
        return int(e.sum() + e2.sum())

    rough, _ = H.split_hair(m, f, roots, 6, 0.0)
    smooth, _ = H.split_hair(m, f, roots, 6, 1.0)
    assert boundary_len(smooth) < boundary_len(rough)
    assert ((smooth > 0) == m).all()


def test_split_handles_disconnected_hair():
    m = np.zeros((100, 200), bool)
    m[10:90, 20:50] = True
    m[10:90, 150:180] = True
    lab, _ = H.split_hair(m, vertical_flow(*m.shape), top_roots(m, 4), 4, 0.3)
    assert ((lab > 0) == m).all() and lab.max() >= 2


def test_empty_hair_mask():
    lab, pivot = H.split_hair(np.zeros((20, 20), bool), vertical_flow(20, 20), np.zeros((0, 2), np.float32), 3, 0.3)
    assert lab.max() == 0


def test_extract_roots_finds_peaks_and_suppresses_neighbours():
    m = curtain()
    heat = np.zeros(m.shape, np.float32)
    for x in (50, 100, 150):
        heat[10, x] = 1.0
    heat[10, 52] = 0.9                                            # too close to the one at 50
    heat[60, 100] = 0.2                                           # below threshold
    heat = cv2.GaussianBlur(heat, (0, 0), 1.0)
    heat /= heat.max()
    r = H.extract_roots(heat, m)
    assert len(r) == 3 and sorted(r[:, 0].tolist()) == pytest.approx([50, 100, 150], abs=2)


def test_streamlines_follow_a_curving_flow_and_stop_at_the_edge():
    h = w = 160
    m = np.zeros((h, w), bool)
    m[:, :] = True
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ang = (yy / h) * 0.9                                          # the strand bends to the right as it goes down
    f = np.stack([np.sin(ang), np.cos(ang)], axis=2).astype(np.float32)
    tr = H.trace_streamlines(f, m, np.array([[20, 0]], np.float32))[0]
    assert tr[-1, 0] > tr[0, 0] + 20 and tr[-1, 1] > 140          # drifted right, ran to the bottom
    assert (np.diff(tr[:, 1]) > 0).all()                          # always moving down
    m2 = m.copy()
    m2[80:, :] = False                                            # the hair ends at row 80
    tr2 = H.trace_streamlines(f, m2, np.array([[20, 0]], np.float32))[0]
    assert tr2[-1, 1] < 90
