import { Suspense, useEffect, useMemo } from "react";
import { Canvas } from "@react-three/fiber";
import { Bounds, Grid, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";

function Model({ url, wireframe, bbox }: { url: string; wireframe: boolean; bbox: boolean }) {
  const { scene } = useGLTF(url);
  const cloned = useMemo(() => scene.clone(true), [scene]);

  useEffect(() => {
    cloned.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if ((mesh as THREE.Mesh).isMesh && mesh.material) {
        const m = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as THREE.MeshStandardMaterial;
        const cl = m.clone();
        cl.wireframe = wireframe;
        cl.metalness = 0.1;
        cl.roughness = 0.6;
        mesh.material = cl;
      }
    });
  }, [cloned, wireframe]);

  const box = useMemo(() => new THREE.Box3().setFromObject(cloned), [cloned]);

  return (
    <group>
      <primitive object={cloned} />
      {bbox && <box3Helper args={[box, new THREE.Color("#22d3ee")]} />}
    </group>
  );
}

export default function GLBViewer({
  url,
  wireframe,
  bbox,
}: {
  url: string;
  wireframe: boolean;
  bbox: boolean;
}) {
  return (
    <Canvas key={url} camera={{ position: [80, 60, 80], fov: 45 }} dpr={[1, 2]}>
      <color attach="background" args={["#06090f"]} />
      <ambientLight intensity={0.7} />
      <directionalLight position={[50, 80, 40]} intensity={1.1} />
      <directionalLight position={[-40, -20, -40]} intensity={0.3} />
      <Suspense fallback={null}>
        <Bounds fit clip observe margin={1.3}>
          <Model url={url} wireframe={wireframe} bbox={bbox} />
        </Bounds>
      </Suspense>
      <Grid
        args={[400, 400]}
        cellSize={10}
        cellColor="#1a2333"
        sectionSize={50}
        sectionColor="#243043"
        position={[0, -0.01, 0]}
        infiniteGrid
        fadeDistance={400}
      />
      <OrbitControls makeDefault enableDamping />
    </Canvas>
  );
}
