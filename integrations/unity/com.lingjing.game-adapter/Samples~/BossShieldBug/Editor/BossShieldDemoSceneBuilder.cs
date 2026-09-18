#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BossShieldDemoSceneBuilder
{
    private const string SceneDirectory = "Assets/LingjingBossShieldDemo";
    private const string ScenePath = SceneDirectory + "/BossShieldDemo.unity";

    [MenuItem("Lingjing/Demo/Create Boss Shield Repro Scene")]
    public static void CreateScene()
    {
        if (!AssetDatabase.IsValidFolder(SceneDirectory))
        {
            AssetDatabase.CreateFolder("Assets", "LingjingBossShieldDemo");
        }

        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

        var boss = GameObject.CreatePrimitive(PrimitiveType.Cube);
        boss.name = "BossShieldFixture";
        boss.transform.position = Vector3.zero;
        boss.transform.localScale = new Vector3(2.8f, 2.8f, 2.8f);
        boss.AddComponent<BossShieldBugFixture>();

        var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
        floor.name = "ArenaFloor";
        floor.transform.position = new Vector3(0f, -1.45f, 0f);
        floor.transform.localScale = new Vector3(0.8f, 1f, 0.8f);

        var cameraObject = new GameObject("Main Camera");
        var camera = cameraObject.AddComponent<Camera>();
        cameraObject.tag = "MainCamera";
        cameraObject.transform.position = new Vector3(0f, 1f, -8f);
        cameraObject.transform.LookAt(boss.transform);
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(0.06f, 0.08f, 0.12f);

        var lightObject = new GameObject("Directional Light");
        var light = lightObject.AddComponent<Light>();
        light.type = LightType.Directional;
        light.intensity = 1.2f;
        lightObject.transform.rotation = Quaternion.Euler(45f, -35f, 0f);

        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene, ScenePath);
        AssetDatabase.SaveAssets();
        Selection.activeGameObject = boss;
        Debug.Log(
            "[LingjingDemo] Boss Shield repro scene created. Press Play with Fixed Behavior off to reproduce; enable it to observe the fixed path."
        );
    }
}
#endif
